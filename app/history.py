"""Undo and redo: a history of project edits, each kept as before and after.

Asked for 2026-09-28. Every edit the user makes - placing, removing,
linking and describing labels, the Snippet Editor's masks, orientation,
ratings and sizes, flags, presets, waypoints - becomes one step that can be
undone and redone.

A step does not remember how to reverse an operation; it remembers the
STATE of what the operation touched, before and after: copies of the
labels by id (with the image they sit on and their place in its list),
the flags of the images, and whichever project lists changed. Undo puts
the "before" copies back, redo the "after". One mechanism for every kind
of edit, so a value added to a label later is undone for free, and the
test for any edit is the same: do, undo, and the project's content digest
is back to what it was.

Only what an edit touches is copied - about 20 microseconds a label - not
the project (20 ms and megabytes at 20,000 labels, and every undo would
redraw every marker).

Qt-free, so it is tested on its own. MainWindow wraps each edit in
``history.edit(...)`` and refreshes what shows the labels afterwards.
"""
import copy
from contextlib import contextmanager
from dataclasses import dataclass, field

DEFAULT_LIMIT = 200

# Project lists a step can carry, as attribute names on LabelProject.
LISTS = ("classes", "descriptions", "mask_names", "waypoints",
         "_next_waypoint_id", "model_review")


@dataclass
class Step:
    """One undoable edit.

    ``labels_*`` map a label id to (image path, index in that image's
    list, label copy), or None where the label did not exist. ``images_*``
    map an image path to a label-less copy of its entry (so its flags,
    and the entry itself when an edit made it, can be put back), or None
    where the project had no entry for it. ``lists_*`` map a LISTS name to a copy
    of its value. ``next_id_*`` is the label id counter, which Clear All
    Labels resets.
    """
    name: str
    labels_before: dict = field(default_factory=dict)
    labels_after: dict = field(default_factory=dict)
    images_before: dict = field(default_factory=dict)
    images_after: dict = field(default_factory=dict)
    lists_before: dict = field(default_factory=dict)
    lists_after: dict = field(default_factory=dict)
    next_id_before: int = 1
    next_id_after: int = 1
    # The project's image paths in order, kept only when the edit
    # changed that order (images removed or brought back): a restored
    # entry returns to its place, not to the end.
    image_order_before: "tuple | None" = None
    image_order_after: "tuple | None" = None
    token: object = None

    def label_ids(self) -> set:
        return set(self.labels_before) | set(self.labels_after)

    def image_paths(self) -> set:
        paths = set(self.images_before) | set(self.images_after)
        for state in list(self.labels_before.values()) + list(
                self.labels_after.values()):
            if state is not None:
                paths.add(state[0])
        return paths

    def is_empty(self) -> bool:
        return (self.labels_before == self.labels_after
                and self.images_before == self.images_after
                and self.lists_before == self.lists_after
                and self.next_id_before == self.next_id_after
                and self.image_order_before == self.image_order_after)


# -- reading and writing project state ---------------------------------------

def _label_state(project, label_id):
    entry = project._label_id_index.get(label_id)
    if entry is None:
        return None
    path, label = entry
    image = project.images.get(path)
    if image is None:
        return None
    index = next((i for i, lab in enumerate(image.labels) if lab is label),
                 len(image.labels))
    return (path, index, copy.deepcopy(label))


def _image_state(project, path):
    image = project.images.get(path)
    if image is None:
        return None
    stub = copy.copy(image)
    stub.labels = []
    return copy.deepcopy(stub)


def _list_state(project, name):
    return copy.deepcopy(getattr(project, name))


def _take_label_out(project, label_id):
    entry = project._label_id_index.get(label_id)
    if entry is None:
        return
    path, label = entry
    project._unindex_label(label)
    image = project.images.get(path)
    if image is not None:
        image.labels = [lab for lab in image.labels if lab is not label]


def _reorder_images(project, order):
    """Put the project's images in ``order``; any not named keep their
    relative order after those that are."""
    images = project.images
    wanted = [path for path in order if path in images]
    placed = set(wanted)
    entries = [(path, images[path]) for path in wanted]
    entries += [(path, image) for path, image in images.items()
                if path not in placed]
    images.clear()
    for path, image in entries:
        images[path] = image


def _apply(project, labels: dict, images: dict, lists: dict, next_id: int,
           image_order=None):
    """Make the project hold exactly these states."""
    for label_id in labels:
        _take_label_out(project, label_id)
    # An entry the project no longer has comes back BEFORE the labels do:
    # undoing a removal of images, its labels need somewhere to return to.
    # (Put back after them, as it once was, they were skipped for want of
    # an image and the entry came back empty.)
    for path, state in images.items():
        if state is not None and path not in project.images:
            project.images[path] = copy.deepcopy(state)
    # Back in list order, so a whole image's labels return to their places.
    ordered = sorted(((state[1], label_id, state)
                      for label_id, state in labels.items()
                      if state is not None), key=lambda item: item[0])
    for index, _label_id, (path, _index, stored) in ordered:
        image = project.images.get(path)
        if image is None:
            continue            # the image left the project; nothing to hold it
        label = copy.deepcopy(stored)   # the stored copy stays pristine
        image.labels.insert(min(index, len(image.labels)), label)
        project._index_label(label, path)
    for path, state in images.items():
        image = project.images.get(path)
        if state is None:
            if image is not None and not image.labels:
                del project.images[path]
            continue
        image.hard_negative_source = state.hard_negative_source
        image.location = state.location
    if image_order is not None:
        _reorder_images(project, image_order)
    for name, value in lists.items():
        setattr(project, name, copy.deepcopy(value))
    project._next_id = next_id


# -- the history ---------------------------------------------------------------

class EditHistory:
    """Undo and redo stacks of Steps, newest last."""

    def __init__(self, limit: int = DEFAULT_LIMIT):
        self.limit = limit
        self._undo: list = []
        self._redo: list = []
        self._listeners: list = []

    # -- asking ---------------------------------------------------------------

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_name(self) -> str:
        return self._undo[-1].name if self._undo else ""

    def redo_name(self) -> str:
        return self._redo[-1].name if self._redo else ""

    def __len__(self):
        return len(self._undo)

    def on_change(self, listener):
        """Call ``listener()`` whenever what can be undone or redone changes."""
        self._listeners.append(listener)

    def _changed(self):
        for listener in list(self._listeners):
            listener()

    # -- recording ------------------------------------------------------------

    @contextmanager
    def edit(self, project, name: str, labels=(), images=(), lists=(),
             all_labels: bool = False, coalesce=None):
        """Record the edit made inside the ``with`` block as one step.

        ``labels`` are the ids the edit may change or remove; labels it
        creates are found by themselves (new ids). ``all_labels`` takes every
        label, for edits that sweep the project (Clear All Labels, Import
        Ground Truth, removing a class). ``images`` are paths whose flags may
        change, ``lists`` the LISTS the edit may change.

        ``coalesce``: a token. A step recorded with the same token and name
        as the newest one joins it - one gesture that reports itself
        several times (Box Link emits one link per label) is one step.

        Nothing is recorded if the block raises, or if nothing changed (a
        dialog cancelled, a value set to what it was).
        """
        label_ids = set(project._label_id_index) if all_labels \
            else set(labels)
        before_labels = {i: _label_state(project, i) for i in label_ids}
        before_images = {p: _image_state(project, p) for p in images}
        order_before = tuple(project.images) if images else None
        before_lists = {n: _list_state(project, n) for n in lists}
        next_before = project._next_id
        yield
        created = set(range(next_before, project._next_id))
        if all_labels:
            created |= set(project._label_id_index)
        for label_id in created - label_ids:
            before_labels[label_id] = None
        order_after = tuple(project.images) if images else None
        if order_before == order_after:
            order_before = order_after = None     # nothing to restore
        step = Step(
            name=name,
            labels_before=before_labels,
            labels_after={i: _label_state(project, i) for i in before_labels},
            images_before=before_images,
            images_after={p: _image_state(project, p) for p in images},
            lists_before=before_lists,
            lists_after={n: _list_state(project, n) for n in lists},
            next_id_before=next_before,
            next_id_after=project._next_id,
            image_order_before=order_before,
            image_order_after=order_after,
            token=coalesce,
        )
        self.push(step)

    def push(self, step: Step):
        if step.is_empty():
            return
        top = self._undo[-1] if self._undo else None
        if (step.token is not None and top is not None
                and top.token == step.token and top.name == step.name):
            self._merge(top, step)
        else:
            self._undo.append(step)
            del self._undo[:-self.limit]
        self._redo.clear()
        self._changed()

    @staticmethod
    def _merge(into: Step, later: Step):
        """``later`` happened straight after ``into``: one step for both."""
        for mine, theirs in ((into.labels_before, later.labels_before),
                             (into.images_before, later.images_before),
                             (into.lists_before, later.lists_before)):
            for key, state in theirs.items():
                mine.setdefault(key, state)
        into.labels_after.update(later.labels_after)
        into.images_after.update(later.images_after)
        into.lists_after.update(later.lists_after)
        into.next_id_after = later.next_id_after
        if later.image_order_after is not None:
            if into.image_order_before is None:
                into.image_order_before = later.image_order_before
            into.image_order_after = later.image_order_after

    # -- going back and forth --------------------------------------------------

    def undo(self, project) -> "Step | None":
        if not self._undo:
            return None
        step = self._undo.pop()
        _apply(project, step.labels_before, step.images_before,
               step.lists_before, step.next_id_before,
               step.image_order_before)
        self._redo.append(step)
        self._changed()
        return step

    def redo(self, project) -> "Step | None":
        if not self._redo:
            return None
        step = self._redo.pop()
        _apply(project, step.labels_after, step.images_after,
               step.lists_after, step.next_id_after,
               step.image_order_after)
        self._undo.append(step)
        self._changed()
        return step

    # -- forgetting ------------------------------------------------------------

    def clear(self):
        """A different project, or an edit history can no longer describe
        (images relocated): start again."""
        if self._undo or self._redo:
            self._undo.clear()
            self._redo.clear()
            self._changed()

    def forget_images(self, paths):
        """Images left the project: drop every step that touches them.

        Undoing one would put labels back on an image that is gone. Steps
        hold states, not operations, so the rest stay correct on their own.
        """
        gone = set(paths)
        if not gone:
            return
        before = (len(self._undo), len(self._redo))
        self._undo = [s for s in self._undo if not (s.image_paths() & gone)]
        self._redo = [s for s in self._redo if not (s.image_paths() & gone)]
        if (len(self._undo), len(self._redo)) != before:
            self._changed()
