#!/usr/bin/env python3
import argparse
import json
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
    in_entries = False
    paths = []
    for raw_line in str(listing_text).splitlines():
        line = raw_line.strip("\r")
        if line.startswith("----------"):
            in_entries = True
            continue
        if not in_entries or not line.startswith("Path = "):
            continue
        paths.append(line[7:].replace("\\", "/"))
    return paths


def write_result(path, payload):
    result_path = Path(path)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = result_path.with_name(result_path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(result_path)


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

    output = (result.stdout or b"").decode("utf-8", errors="replace")
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
    if args.once:
        run_once(request_dir)
        return 0

    while True:
        run_once(request_dir)
        time.sleep(max(args.poll_ms, 10) / 1000.0)


if __name__ == "__main__":
    raise SystemExit(main())
