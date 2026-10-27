"""The order of the classes in the Model Review window (2.9.8): the
user's, so a key number can be put on the class they want.

The keys 1-9 and Shift+1-9 pick classes by their place in the window's
list, which was the project's order - a fact of history, not a choice.
Now a class dragged up or down the Labels panel changes the window's
order, kept in the settings by name, so it holds across sessions and
across projects that share class names. The project's own list is not
touched: its order gives every class its colour, and the map and the
other windows read it.

``ordered`` is the rule: the saved names the project has, in the saved
order, then the project's others in the project's order.
"""
from __future__ import annotations

import json

ORDER_KEY = "model_review/class_order"


def ordered(project_names, saved) -> list:
    """The project's classes in the user's order: see the module."""
    project = list(project_names)
    have = set(project)
    result: list = []
    for name in saved:
        if name in have and name not in result:
            result.append(name)
    result += [name for name in project if name not in result]
    return result


def load_order(settings) -> list:
    """The saved order, a list of names; nothing where there is none or
    the value is not what was written."""
    raw = settings.value(ORDER_KEY, "")
    try:
        names = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(name) for name in names] if isinstance(names, list) else []


def save_order(settings, names) -> None:
    settings.setValue(ORDER_KEY, json.dumps([str(name) for name in names]))


def with_rename(names, old: str, new: str) -> list:
    """The order with ``old`` called ``new``: a renamed class keeps its
    place under its key."""
    return [new if name == old else name for name in names]
