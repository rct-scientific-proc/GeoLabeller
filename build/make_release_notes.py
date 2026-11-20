"""Write docs/RELEASE_NOTES.md - every release's notes, newest first.

Asked for 2026-11-20: a Release Notes page in the Help menu. The notes
already exist: every release is an annotated tag whose message says what
changed. This gathers them into one file that ships with the application
(build/setup.py), so Help > Release Notes works installed and offline.

Only releases that were published: tags that are on GitHub (origin). A
tag made locally and never pushed - v1.3.0, a release that did not go out
- is no release. Without a connection to origin, every local tag is used
and a warning says so.

The notes ship to every user, so wording meant for the people who make
GeoLabeller is reworded on the way in (REWORDINGS; Ryan, 2026-11-20: no
"ML team"), and a phrase that must not ship (MUST_NOT_SHIP) stops the run
with the release that carries it, rather than reaching a user.

The release being made has no tag yet when its release commit is made -
the tag points at that commit - so its notes are passed in:

    python build/make_release_notes.py --next 2.10.6 --notes tag_2.10.6.txt

writes the file with 2.10.6 on top (today's date), followed by every
v*.*.* tag. Without --next it is the tags alone. The release workflow
refuses to build a version the file does not name, as it refuses one
VERSION does not.

File format (Markdown, readable on GitHub too; app/release_notes.py
parses it for the Help window):

    ## 2.10.5 (2026-11-19)
    ## 1.2.0 (2026-08-17) - very large mosaics

then the tag's message, less a first line that only names the release
("Release 2.10.5", "GeoLabeller 1.1.0") and any trailer lines
(Co-Authored-By:, Signed-off-by:).
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "RELEASE_NOTES.md"
VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
TRAILER = re.compile(r"^(Co-Authored-By|Signed-off-by):", re.IGNORECASE)
# "Release 2.10.5", "GeoLabeller 1.1.0", "GeoLabeller 1.2.0 - very large
# mosaics": the name of the release, and a title after it if there is one.
SUBJECT = re.compile(r"^(?:Release|GeoLabeller)\s+v?\d+\.\d+\.\d+"
                     r"(?:\s+-\s+(?P<title>.+))?\s*$")

HEADER = ("# GeoLabeller release notes\n\n"
          "Newest first. Each release's notes are the message of its "
          "release tag on GitHub.\n")

# (pattern, replacement): wording in old tag messages that is not for
# users, rewritten. Whitespace in a pattern is \s+, since a phrase may wrap
# across lines; the replacement rejoins it on one.
REWORDINGS = [
    (r"a JSON file from the ML\s+team\s+listing\s+the\s+chips\s+their\s+"
     r"classifier", "a JSON file listing the chips a classifier"),
    (r"The\s+file\s+format\s+for\s+the\s+ML\s+team\s+is",
     "The review file's format is"),
    (r"The\s+next\s+value\s+the\s+ML\s+team\s+asks\s+for",
     "The next value asked for"),
]
MUST_NOT_SHIP = [r"\bML\s+team\b"]


def version_key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def published_tags() -> "set | None":
    """The tag names on origin (GitHub), or None when it cannot be asked."""
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--tags", "origin", "refs/tags/v*"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {line.split("refs/tags/", 1)[1].removesuffix("^{}")
            for line in out.splitlines() if "refs/tags/" in line}


def tag_records(published: "set | None" = None) -> list:
    """(version, date, message) for every v*.*.* tag, from git - only
    those in ``published`` when it is given."""
    out = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)%00"
         "%(creatordate:short)%00%(contents)%01", "refs/tags/v*"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        check=True).stdout
    records = []
    for chunk in out.split("\x01"):
        if not chunk.strip():
            continue
        name, date, message = chunk.lstrip("\n").split("\x00", 2)
        name = name.strip()
        if VERSION_TAG.match(name) and (published is None
                                        or name in published):
            records.append((name[1:], date.strip(), message))
    return records


def reword(message: str) -> str:
    """The message with REWORDINGS applied."""
    for pattern, replacement in REWORDINGS:
        message = re.sub(pattern, replacement, message)
    return message


def entry(version: str, date: str, message: str) -> str:
    """One release's section of the file."""
    message = reword(message)
    for pattern in MUST_NOT_SHIP:
        found = re.search(pattern, message)
        if found:
            raise SystemExit(
                f"The notes of {version} say {found.group()!r}, which must "
                "not ship: reword its tag message or add a rewording to "
                "build/make_release_notes.py (REWORDINGS).")
    lines = [line.rstrip() for line in message.strip().splitlines()]
    lines = [line for line in lines if not TRAILER.match(line.strip())]
    title = ""
    if lines:
        named = SUBJECT.match(lines[0].strip())
        if named:
            title = (named.group("title") or "").strip()
            lines = lines[1:]
    body = "\n".join(lines).strip()
    heading = f"## {version} ({date})" + (f" - {title}" if title else "")
    return heading + "\n\n" + (body or "No notes were written for this "
                               "release.") + "\n"


def build(records: list) -> str:
    """The file, newest version first; a version named twice keeps its
    first record (the one passed in with --next, before any tag)."""
    seen, sections = set(), []
    for version, date, message in sorted(
            records, key=lambda r: version_key(r[0]), reverse=True):
        if version in seen:
            continue
        seen.add(version)
        sections.append(entry(version, date, message))
    return HEADER + "\n" + "\n".join(sections)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--next", help="the version being released, X.Y.Z")
    parser.add_argument("--notes", help="a file holding its notes (the "
                        "message its tag will carry)")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    records = []
    if args.next:
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.next) or not args.notes:
            parser.error("--next takes X.Y.Z and needs --notes")
        message = Path(args.notes).read_text(encoding="utf-8")
        records.append((args.next, datetime.date.today().isoformat(),
                        message))
    published = published_tags()
    if published is None:
        print("warning: could not ask origin which tags were published; "
              "every local release tag is used", file=sys.stderr)
    records += tag_records(published)
    Path(args.out).write_text(build(records), encoding="utf-8",
                              newline="\n")
    print(f"Wrote {args.out}: {len({r[0] for r in records})} releases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
