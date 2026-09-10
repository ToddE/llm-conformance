#!/usr/bin/env python3
"""Regenerate .env.example from .env, stripping every value.

Exists because `cp .env .env.example` is a one-keystroke way to commit a live
credential, and it has already happened once in this repo's history. The
template must be GENERATED, never copied.

Also usable as a pre-commit check:  python3 scripts/sync-env-example.py --check
"""
from __future__ import annotations

import re
import sys

VALUE_LINE = re.compile(r'^(\s*)([A-Za-z_][A-Za-z0-9_]*)=(.*)$')

# Anything matching these in a tracked file is a hard failure.
SECRET_SHAPES = [
    re.compile(r'sk-ant-api\d+-[A-Za-z0-9_-]{20,}'),
    re.compile(r'sk-proj-[A-Za-z0-9_-]{20,}'),
    re.compile(r'\bAIza[0-9A-Za-z_-]{30,}'),
    re.compile(r'sk-[A-Za-z0-9]{32,}'),
    re.compile(r'\bASIA[0-9A-Z]{16}\b'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
]


def strip_values(src: str) -> str:
    out = []
    for line in src.splitlines(keepends=True):
        m = VALUE_LINE.match(line.rstrip("\n"))
        out.append(f"{m.group(1)}{m.group(2)}=\n" if m and m.group(3).strip() else line)
    return "".join(out)


def scan(path: str, text: str) -> list[str]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat in SECRET_SHAPES:
            if pat.search(line):
                hits.append(f"{path}:{i}: matches {pat.pattern}")
    return hits


def main() -> int:
    check = "--check" in sys.argv
    try:
        src = open(".env", encoding="utf-8").read()
    except FileNotFoundError:
        print("no .env — nothing to sync")
        return 0

    generated = strip_values(src)

    leaks = scan(".env.example", generated)
    if leaks:
        print("REFUSING: generated template still contains secret-shaped text:")
        for h in leaks:
            print("  " + h)
        return 2

    try:
        current = open(".env.example", encoding="utf-8").read()
    except FileNotFoundError:
        current = None

    if check:
        existing_leaks = scan(".env.example", current or "")
        if existing_leaks:
            print("FAIL: .env.example contains secret-shaped text:")
            for h in existing_leaks:
                print("  " + h)
            return 2
        if current != generated:
            print("FAIL: .env.example is out of sync with .env "
                  "(run: python3 scripts/sync-env-example.py)")
            return 1
        print("OK: .env.example is value-free and in sync")
        return 0

    open(".env.example", "w", encoding="utf-8").write(generated)
    n = sum(1 for l in src.splitlines() if (m := VALUE_LINE.match(l)) and m.group(3).strip())
    print(f"wrote .env.example — {n} value(s) stripped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
