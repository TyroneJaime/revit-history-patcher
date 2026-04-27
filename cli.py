"""
cli.py — Command-line interface for the RVT History Patcher.

Examples
--------
    python cli.py detect project.rvt
    python cli.py patch project.rvt --old "old.user1234" --new "new.user5678"
    python cli.py patch project.rvt --new "new.user5678" -o patched.rvt
"""

from __future__ import annotations

import argparse
import sys

import patcher


def _cmd_detect(args: argparse.Namespace) -> int:
    results = patcher.detect_usernames(args.file)
    if not results:
        print("No usernames found in stream.")
        return 1
    print(f"Usernames in {args.file}:")
    for name, count in results:
        print(f"  {name}  —  {count} save entries  ({len(name)} chars)")
    return 0


def _cmd_patch(args: argparse.Namespace) -> int:
    old = args.old
    if not old:
        results = patcher.detect_usernames(args.file)
        if not results:
            print("Error: no usernames found in file. Specify --old explicitly.", file=sys.stderr)
            return 2
        old = results[0][0]
        print(f"Auto-detected current username: {old} ({results[0][1]} entries)")

    try:
        result = patcher.patch_rvt(
            rvt_path=args.file,
            old_user=old,
            new_user=args.new,
            output_path=args.output,
        )
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"\nPatched {result['replaced']} entries, {result['stream_bytes']} bytes written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rvt-history-patcher",
        description="Patch save-history usernames inside Revit .rvt files.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_detect = sub.add_parser("detect", help="List usernames recorded in the file's history stream")
    p_detect.add_argument("file", help="Path to the .rvt file")
    p_detect.set_defaults(func=_cmd_detect)

    p_patch = sub.add_parser("patch", help="Replace a username in the history stream")
    p_patch.add_argument("file", help="Path to the .rvt file")
    p_patch.add_argument("--old", help="Current username (auto-detected if omitted)")
    p_patch.add_argument("--new", required=True, help="Replacement username (must be <= current length)")
    p_patch.add_argument("-o", "--output", help="Output path (in-place if omitted)")
    p_patch.set_defaults(func=_cmd_patch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
