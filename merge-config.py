#!/usr/bin/env python3
"""Merge new keys from crashdog.default.yaml into an existing config."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def merge(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if key in dst and isinstance(dst[key], dict) and isinstance(value, dict):
            merge(dst[key], value)
        elif key not in dst:
            dst[key] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge CrashDog default config keys")
    parser.add_argument("--default", type=Path, default=Path(__file__).resolve().parent / "crashdog.default.yaml")
    parser.add_argument("--target", type=Path, default=Path("/etc/crashdog/config.yaml"))
    args = parser.parse_args(argv)

    if not args.default.exists():
        print(f"Default config not found: {args.default}", file=sys.stderr)
        return 1
    if not args.target.exists():
        print(f"Target config not found: {args.target}", file=sys.stderr)
        return 1

    default = yaml.safe_load(args.default.read_text(encoding="utf-8")) or {}
    existing = yaml.safe_load(args.target.read_text(encoding="utf-8")) or {}
    before = set(existing)
    merge(existing, default)
    added = [key for key in existing if key not in before]
    args.target.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
    print(f"Merged into {args.target}")
    if added:
        print("Added top-level keys:", ", ".join(added))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())