#!/usr/bin/env python3
"""Check public files for runtime data, obvious credentials and broken docs."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
PRIVATE_SUFFIXES = {".sqlite", ".db", ".log", ".pem", ".key", ".pyc"}
TOKEN = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-[A-Za-z0-9_-]{25,})")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def public_files():
    if (ROOT / ".git").is_dir():
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, check=True, capture_output=True,
        )
        return [ROOT / p.decode() for p in result.stdout.split(b"\0") if p]
    return [p for p in ROOT.rglob("*") if p.is_file()
            and not any(x in IGNORED for x in p.relative_to(ROOT).parts)
            and p.name != ".release-interface.json"]


def main():
    problems = []
    paths = public_files()
    for p in paths:
        name = p.relative_to(ROOT).as_posix()
        if p.is_symlink():
            problems.append(f"{name}: symbolic link is not a release artifact")
            continue
        if p.suffix in PRIVATE_SUFFIXES or p.name in {"auth.json", "connection.json", ".env"}:
            problems.append(f"{name}: runtime or private file")
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeError:
            problems.append(f"{name}: unexpected binary file")
            continue
        if TOKEN.search(content):
            problems.append(f"{name}: possible credential (value hidden)")
        if re.search(r"/(?:home|Users)/[A-Za-z0-9_.-]+/", content):
            problems.append(f"{name}: machine-specific home path")
        if content and not content.endswith("\n"):
            problems.append(f"{name}: missing final newline")
        # Unified-diff context lines retain their required leading space.
        if p.suffix != ".patch" and any(line.rstrip() != line for line in content.splitlines()):
            problems.append(f"{name}: trailing whitespace")
        if p.suffix != ".md":
            continue
        fence = False
        headings = []
        for num, line in enumerate(content.splitlines(), 1):
            if line.startswith("```"):
                if not fence and not line[3:].strip():
                    problems.append(f"{name}:{num}: code fence needs a language")
                fence = not fence
                continue
            if fence:
                continue
            h = re.match(r"^(#{1,6})\s", line)
            if h:
                level = len(h[1])
                if headings and level > headings[-1] + 1:
                    problems.append(f"{name}:{num}: heading level skipped")
                headings.append(level)
            for match in LINK.finditer(line):
                target = match[1].strip("<>").split("#", 1)[0]
                if not target or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                    continue
                if not (p.parent / unquote(target)).exists():
                    problems.append(f"{name}:{num}: broken relative link {target}")
        if fence:
            problems.append(f"{name}: unclosed code fence")
        if headings.count(1) != 1:
            problems.append(f"{name}: expected one top-level heading")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"Release checks passed ({len(paths)} text files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
