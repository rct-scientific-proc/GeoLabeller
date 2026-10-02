"""Who is working: a user name, and the stamp an edit carries.

Asked for 2026-09-30. Users want, in later builds, several people to
label the same objects, draw the same masks and give the same ratings, and
a final reviewer to compare their results. This is the first part: the
naming. Each person enters a user name (no password - it is not meant to
be secure; the project file is plain JSON anyone can edit), and what they
do carries it: who created a label, and who last set its class,
description, group ID, link, orientation, size, confidence, review and
each of its masks, with when (UTC).

Stored on the label as ``attribution`` (format 4.8) and on each mask as
``by`` / ``at``, and carried in the project and every ground truth
export. Nothing is stamped while no user name is set, so attribution
present always means a known person.
"""
from datetime import datetime, timezone

from .settings_scope import settings

_KEY = "identity/user"
MAX_LENGTH = 64

# What an attribution records on a label, by key.
FIELDS = ("created", "class", "description", "group_id", "link",
          "orientation", "size", "confidence", "review", "masks",
          "position")


def clean(name) -> str:
    """A user name as stored: trimmed, single-line, bounded."""
    text = " ".join(str(name or "").split())
    return text[:MAX_LENGTH]


def current_user() -> str:
    return clean(settings().value(_KEY, "") or "")


def set_user(name: str) -> str:
    name = clean(name)
    settings().setValue(_KEY, name)
    return name


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp(user: "str | None" = None) -> "dict | None":
    """{"by": user, "at": now} - or None when nobody is set."""
    user = clean(current_user() if user is None else user)
    return {"by": user, "at": now()} if user else None


def valid_attribution(data) -> dict:
    """An attribution read from a file, keeping only well-formed entries."""
    if not isinstance(data, dict):
        return {}
    kept = {}
    for key, value in data.items():
        if (isinstance(key, str) and isinstance(value, dict)
                and isinstance(value.get("by"), str) and value["by"]):
            entry = {"by": clean(value["by"])}
            if isinstance(value.get("at"), str):
                entry["at"] = value["at"]
            kept[key] = entry
    return kept
