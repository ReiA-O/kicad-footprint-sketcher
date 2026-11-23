"""Check tracked files (optionally history) and nested archives before publishing.

Prints locations and finding kinds, never matched values. Uses only stdlib.
"""
from __future__ import annotations
import argparse
import io
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "local-home": re.compile(r"/(?:home|Users)/[^/\s\"'`<>]+|\b[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+", re.I),
    "local-workspace": re.compile(r"\b[A-Za-z]:[\\/]+CodexWork(?:[\\/]|\b)|/run/user/\d+|(?<![\w])/(?:var/)?tmp/", re.I),
    "private-key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    "access-token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{25,}|AKIA[0-9A-Z]{16}"),
    "credential-url": re.compile(r"https?://[^/\s:@]+:[^/\s@]+@", re.I),
}
MAX_MEMBER = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def findings(data, label, *, depth=0, budget=None):
    budget = [0] if budget is None else budget
    if depth > 12:
        return [(label, "archive-depth")]
    result = []
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                if entry.flag_bits & 1 or entry.file_size > MAX_MEMBER:
                    result.append((label, "unreadable-archive-member")); continue
                budget[0] += entry.file_size
                if budget[0] > MAX_EXPANDED:
                    return result + [(label, "expanded-size")]
                result += check_text(entry.filename + " " + entry.comment.decode("utf-8", "replace"), label + "!member-name")
                result += findings(archive.read(entry), label + "!" + entry.filename.replace("\\", "/"), depth=depth+1, budget=budget)
            result += check_text(archive.comment.decode("utf-8", "replace"), label + "!archive-comment")
    elif b"\0" not in data:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return result
        result += check_text(text, label)
    return result


def check_text(text, label):
    return [(label, kind) for kind, pattern in PATTERNS.items() if pattern.search(text)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Also inspect blobs and commit metadata in all local refs.")
    args = parser.parse_args()
    problems, scanned = [], 0
    if args.history:
        for item in git("rev-list", "--objects", "--all").decode().splitlines():
            parts = item.split(" ", 1)
            sha = parts[0]
            kind = git("cat-file", "-t", sha).strip()
            if kind == b"blob":
                problems += findings(git("cat-file", "blob", sha), parts[1] if len(parts)>1 else "history-blob")
                scanned += 1
            elif kind == b"commit":
                problems += check_text(git("cat-file", "commit", sha).decode("utf-8", "replace"), "commit-metadata")
    else:
        for raw in git("ls-files", "-z").split(b"\0"):
            if raw:
                name = raw.decode(); path = ROOT / name
                if path.is_file():
                    problems += findings(path.read_bytes(), name); scanned += 1
    for label, kind in sorted(set(problems)):
        print(f"{kind}: {label}")
    print(f"Publication check: {scanned} files/blobs, {len(set(problems))} findings")
    return bool(problems)


if __name__ == "__main__":
    sys.exit(main())
