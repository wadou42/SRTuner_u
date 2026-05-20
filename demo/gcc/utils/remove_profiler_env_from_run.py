#!/usr/bin/env python3
import argparse
from pathlib import Path


PROFILER_ENV = (
    b"LD_PRELOAD=/usr/local/lib/libprofiler.so.0 "
    b"CPUPROFILE=./main.prof "
    b"CPUPROFILE_FREQUENCY=1000"
)


def patch_run_file(path: Path, dry_run: bool) -> bool:
    data = path.read_bytes()
    patched = data.replace(PROFILER_ENV + b" ", b"").replace(PROFILER_ENV, b"")
    if patched == data:
        return False

    if not dry_run:
        path.write_bytes(patched)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Remove gperftools profiler environment variables from all __run files "
            "under a directory."
        )
    )
    parser.add_argument("--root", help="Directory to search recursively.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list files that would be modified.",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"Not a directory: {root}")

    changed = 0
    for run_file in sorted(root.rglob("__run")):
        if not run_file.is_file():
            continue
        if patch_run_file(run_file, args.dry_run):
            changed += 1
            action = "would update" if args.dry_run else "updated"
            print(f"{action}: {run_file}")

    print(f"matched files changed: {changed}")


if __name__ == "__main__":
    main()
