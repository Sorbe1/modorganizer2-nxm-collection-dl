#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def seven_zip_executable():
    executable = shutil.which("7z") or shutil.which("7zz")
    if executable:
        return executable
    for candidate in (
        "/usr/bin/7z",
        "/usr/local/bin/7z",
        "/bin/7z",
        "/usr/bin/7zz",
        "/usr/local/bin/7zz",
        "/bin/7zz",
    ):
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return None


def seven_zip_member_paths(listing_text):
    """Return file/member paths from a ``7z l -slt`` listing."""
    if isinstance(listing_text, bytes):
        listing_text = listing_text.decode("utf-8", errors="replace")
    in_entries = False
    paths = []
    current_path = None
    current_is_directory = False

    def flush_entry():
        if current_path and not current_is_directory:
            paths.append(current_path.replace("\\", "/"))

    for raw_line in str(listing_text).splitlines():
        line = raw_line.strip("\r")
        if line.startswith("----------"):
            in_entries = True
            continue
        if not in_entries:
            continue
        if not line:
            flush_entry()
            current_path = None
            current_is_directory = False
            continue
        if line.startswith("Path = "):
            flush_entry()
            current_path = line[7:]
            current_is_directory = False
            continue
        if line.startswith("Attributes = "):
            attributes = line[len("Attributes = ") :].casefold()
            current_is_directory = "d" in attributes
    flush_entry()
    return paths


def seven_zip_module_config_path(listing_text):
    """Return the FOMOD ModuleConfig path from a 7z ``l -slt`` listing."""
    if isinstance(listing_text, bytes):
        listing_text = listing_text.decode("utf-8", errors="replace")
    for raw_line in str(listing_text).splitlines():
        if not raw_line.startswith("Path = "):
            continue
        candidate = raw_line[7:].strip().replace("\\", "/")
        if candidate.casefold().endswith("fomod/moduleconfig.xml"):
            return candidate
    return None


def write_result(path, payload):
    result_path = Path(path)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = result_path.with_name(result_path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(result_path)


def heartbeat_path(request_dir):
    return Path(request_dir) / "native-archive-worker.heartbeat.json"


def write_heartbeat(request_dir):
    write_result(
        heartbeat_path(request_dir),
        {
            "ok": True,
            "pid": os.getpid() if hasattr(os, "getpid") else None,
            "time": time.time(),
        },
    )


def decode_7z_stdout(data):
    """Decode 7z stdout, preserving UTF-16 XML extracted with ``-so``."""
    raw = bytes(data or b"")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeError:
            pass

    sample = raw[:200]
    if sample and sample.count(b"\x00") > max(4, len(sample) // 8):
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                decoded = raw.decode(encoding)
            except UnicodeError:
                continue
            if "\x00" not in decoded[:100]:
                return decoded

    return raw.decode("utf-8", errors="replace")


def run_7z(args, timeout):
    executable = seven_zip_executable()
    if not executable:
        return {
            "ok": False,
            "stdout": "",
            "error": "No 7z/7zz executable available.",
        }
    try:
        result = subprocess.run(
            [executable] + args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except Exception as e:
        return {"ok": False, "stdout": "", "error": f"Could not run 7z: {e}"}

    output = decode_7z_stdout(result.stdout)
    if result.returncode != 0:
        return {
            "ok": False,
            "stdout": output,
            "error": f"7z exited {result.returncode}: {output.strip()}",
        }
    return {"ok": True, "stdout": output, "error": None}


def handle_request(payload):
    action = str(payload.get("action") or "").strip()
    archive = str(payload.get("archive") or "").strip()
    if not archive:
        return {"ok": False, "error": "Request missing archive path."}

    if action == "list":
        result = run_7z(["l", "-slt", archive], timeout=60)
        if not result["ok"]:
            return {"ok": False, "error": result["error"]}
        return {"ok": True, "members": seven_zip_member_paths(result["stdout"])}

    if action == "fomod":
        listing = run_7z(["l", "-slt", archive], timeout=60)
        if not listing["ok"]:
            return {"ok": False, "error": listing["error"]}
        module_path = seven_zip_module_config_path(listing["stdout"])
        if not module_path:
            return {
                "ok": True,
                "has_fomod": False,
                "module_config": None,
                "module_xml": None,
            }
        extraction = run_7z(["x", "-so", archive, module_path], timeout=60)
        if not extraction["ok"]:
            return {"ok": False, "error": extraction["error"]}
        if not extraction["stdout"]:
            return {"ok": False, "error": "7z did not return FOMOD XML content."}
        return {
            "ok": True,
            "has_fomod": True,
            "module_config": module_path,
            "module_xml": extraction["stdout"],
        }

    if action == "extract":
        target_dir = str(payload.get("target_dir") or "").strip()
        if not target_dir:
            return {"ok": False, "error": "Extract request missing target_dir."}
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        result = run_7z(["x", "-y", f"-o{target_dir}", archive], timeout=300)
        if not result["ok"]:
            return {"ok": False, "error": result["error"]}
        return {"ok": True}

    return {"ok": False, "error": f"Unsupported action: {action}"}


def process_request(path):
    request_path = Path(path)
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"Could not read request {request_path}: {e}"

    result_path = payload.get("result_path")
    if not result_path:
        return False, f"Request {request_path} missing result_path."
    result = handle_request(payload)
    write_result(result_path, result)
    try:
        request_path.unlink()
    except OSError:
        pass
    return True, None


def run_once(request_dir):
    request_paths = sorted(Path(request_dir).glob("*.request.json"))
    processed = 0
    for request_path in request_paths:
        ok, error = process_request(request_path)
        if not ok:
            print(error, flush=True)
        processed += 1
    return processed


def main():
    parser = argparse.ArgumentParser(description="NXM collection archive worker")
    parser.add_argument("request_dir")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-ms", type=int, default=50)
    args = parser.parse_args()

    request_dir = Path(args.request_dir)
    request_dir.mkdir(parents=True, exist_ok=True)
    write_heartbeat(request_dir)
    if args.once:
        run_once(request_dir)
        write_heartbeat(request_dir)
        return 0

    while True:
        write_heartbeat(request_dir)
        run_once(request_dir)
        time.sleep(max(args.poll_ms, 10) / 1000.0)


if __name__ == "__main__":
    raise SystemExit(main())
