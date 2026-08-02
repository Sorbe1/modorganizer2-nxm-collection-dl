#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_helpers import compareMo2ProfileStateSnapshots


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()

    result = compareMo2ProfileStateSnapshots(args.left, args.right)
    print(f"Left: {result['left']}")
    print(f"Right: {result['right']}")
    print(f"Changed: {'yes' if result['changed'] else 'no'}")
    if result["changed_files"]:
        print("Changed profile files: " + ", ".join(result["changed_files"]))
    else:
        print("Changed profile files: none")
    print(
        "Download metadata changed: "
        f"{'yes' if result['download_metadata_changed'] else 'no'}"
    )


if __name__ == "__main__":
    main()
