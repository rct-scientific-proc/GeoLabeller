"""The PDF report: the numbers, then the chips themselves.

A cover with the embeddings compared and the tables for the one shown;
then, for each predicted class, its over-clustered clusters as contact
sheets - every chip captioned with its verdict, a colour bar per verdict
class, a red frame on a chip that disagrees with its cluster's majority;
the seeds and the mistakes of one seeded fill-in; and the chips farthest
from everything the model knows, the ones a reviewer should see first.
"""
from __future__ import annotations

import io
from datetime import datetime

import numpy as np
from fpdf import FPDF
from fpdf.fonts import FontFace
from PIL import Image

from app.model_review.ledger import IGNORE
from research.partition import methods

PAGE_W, PAGE_H = 210.0, 297.0
MARGIN = 12.0
BODY_W = PAGE_W - 2 * MARGIN
FOOT = 14.0
THUMB = 19.0                    # mm
GAP = 2.0
PER_ROW = 8
CAPTION_H = 3.2
ROW_H = THUMB + 1.2 + CAPTION_H + 1.5
MAX_PER_CLUSTER = 16
MAX_SHEET = 24
THUMB_PX = 72
SEEDS_SHOWN = 3
PALETTE = ((31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40),
           (148, 103, 189), (140, 86, 75), (227, 119, 194), (188, 189, 34),
           (23, 190, 207), (255, 187, 120), (152, 223, 138), (197, 176, 213))
GREY = (140, 140, 140)
RED = (205, 30, 30)
ORANGE = (235, 140, 0)


def _safe(text) -> str:
    """Helvetica is a Latin-1 font; a class name may not be."""
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _pct(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (tuple, list)):
        return f"{100 * value[0]:.0f} +/- {100 * value[1]:.0f}"
    return f"{100 * value:.0f}"


def _jpeg(arr: np.ndarray) -> io.BytesIO:
    image = Image.fromarray(np.ascontiguousarray(arr[..., :3]))
    image = image.resize((THUMB_PX, THUMB_PX), Image.BILINEAR)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=85)
    buf.seek(0)
    return buf


class Report(FPDF):

    def __init__(self, name: str):
        super().__init__("P", "mm", "A4")
        self.name = name
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(True, margin=FOOT)
        self.set_title(_safe(f"Error partitioning: {name}"))

    def footer(self):
        self.set_y(-10)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(120, 120, 120)
        self.cell(BODY_W / 2, 4, _safe(f"Error partitioning: {self.name}"))
        self.cell(BODY_W / 2, 4, str(self.page_no()), align="R")
        self.set_text_color(0, 0, 0)

    def need(self, height: float):
        if self.get_y() + height > PAGE_H - FOOT:
            self.add_page()

    def h1(self, text: str):
        self.set_font("Helvetica", "B", 15)
        self.multi_cell(BODY_W, 7.5, _safe(text))
        self.ln(1)

    def h2(self, text: str, keep: float = 14.0):
        """``keep``: the height that must follow it on the same page."""
        self.need(keep)
        self.set_font("Helvetica", "B", 10.5)
        self.multi_cell(BODY_W, 5.5, _safe(text))
        self.ln(0.3)

    def para(self, text: str, size: float = 8.8):
        self.set_font("Helvetica", "", size)
        self.multi_cell(BODY_W, 4.2, _safe(text))
        self.ln(1.2)

    def rows(self, rows: list, widths=None):
        """A table; the first row is its heading."""
        self.set_font("Helvetica", "", 7.6)
        self.set_fill_color(255, 255, 255)
        align = ("LEFT",) + ("CENTER",) * (len(rows[0]) - 1)
        with self.table(col_widths=widths, text_align=align, line_height=4.3,
                        padding=0.6, borders_layout="MINIMAL",
                        headings_style=FontFace(emphasis="BOLD",
                                                fill_color=(226, 226, 226)),
                        cell_fill_color=(245, 245, 245),
                        cell_fill_mode="ROWS") as table:
            for data in rows:
                row = table.row()
                for datum in data:
                    row.cell(_safe(datum))
        self.ln(2)

    def legend(self, colours: dict):
        self.set_font("Helvetica", "", 7.5)
        x, y = MARGIN, self.get_y()
        for name, colour in colours.items():
            width = self.get_string_width(_safe(name)) + 7
            if x + width > PAGE_W - MARGIN:
                x, y = MARGIN, y + 4.5
            self.set_fill_color(*colour)
            self.rect(x, y + 0.6, 3, 3, style="F")
            self.set_xy(x + 4, y)
            self.cell(width - 4, 4.2, _safe(name))
            x += width + 2
        self.set_xy(MARGIN, y + 5.5)

    def sheet(self, chips: list, captions: list, bars: list, frames: list,
              more: int = 0):
        """Rows of thumbnails: a colour bar under each, a caption, and a
        frame where one is given."""
        for start in range(0, len(chips), PER_ROW):
            self.need(ROW_H)
            y = self.get_y()
            for col, i in enumerate(range(start, min(len(chips),
                                                     start + PER_ROW))):
                x = MARGIN + col * (THUMB + GAP)
                self.image(_jpeg(chips[i]), x=x, y=y, w=THUMB, h=THUMB)
                if bars[i] is not None:
                    self.set_fill_color(*bars[i])
                    self.rect(x, y + THUMB, THUMB, 1.2, style="F")
                if frames[i] is not None:
                    self.set_draw_color(*frames[i])
                    self.set_line_width(0.55)
                    self.rect(x + 0.25, y + 0.25, THUMB - 0.5, THUMB - 0.5)
                    self.set_line_width(0.2)
                    self.set_draw_color(0, 0, 0)
                self.set_xy(x, y + THUMB + 1.4)
                self.set_font("Helvetica", "", 5.8)
                self.cell(THUMB, CAPTION_H, _safe(captions[i])[:15], align="C")
            self.set_xy(MARGIN, y + ROW_H)
        if more:
            self.set_font("Helvetica", "I", 7.5)
            self.cell(BODY_W, 4, f"... and {more} more", align="L")
            self.ln(5)
        else:
            self.ln(1)


def _comparison(results: dict) -> list:
    rows = [["embedding", "all chips, k=10 k-means purity",
             "3 seeds/class, spreading", "cluster then label (k=10): "
             "clicks / accuracy", "new-class AUROC"]]
    for name, res in results.items():
        if "error" in res:
            rows.append([name, "failed", "-", "-", "-"])
            continue
        over = res["groups"].get("all", {}).get("methods", {}).get(
            "kmeans@over_k", {})
        seeded = res.get("seeded", {}).get(3, {})
        then = res.get("cluster_then_label", {}).get("kmeans_over", {})
        novelty = res.get("novelty") or {}
        rows.append([
            name, _pct(over.get("purity")),
            _pct(seeded.get("spreading")),
            (f"{then.get('clicks', '-')} / {_pct(then.get('accuracy'))}"
             if then else "-"),
            _pct(novelty.get("prototype_auroc"))])
    return rows


def _clustering_rows(res: dict) -> list:
    rows = [["predicted", "chips", "kinds", "k", "k-means purity", "clicks",
             "spectral purity", "clicks", "HDBSCAN purity", "clicks"]]
    for group, g in res["groups"].items():
        if g.get("skipped"):
            rows.append([group, str(g["n"]), "-", "-", "skipped", "", "",
                         "", "", ""])
            continue
        m = g["methods"]
        rows.append([
            group, str(g["n"]), str(g["k_true"]), str(g["k_over"]),
            _pct(m["kmeans@over_k"]["purity"]),
            str(m["kmeans@over_k"]["clicks"]),
            _pct(m["spectral@over_k"]["purity"]),
            str(m["spectral@over_k"]["clicks"]),
            _pct(m["hdbscan"]["purity"]),
            f"{m['hdbscan']['clicks']} ({m['hdbscan']['unclustered']} alone)"])
    return rows


def _seeded_rows(res: dict) -> list:
    rows = [["seeds per class", "label spreading", "kNN vote",
             "logistic head", "coverage at 90 % (spreading / logistic)"]]
    for per_class, s in res.get("seeded", {}).items():
        rows.append([str(per_class), _pct(s["spreading"]), _pct(s["knn"]),
                     _pct(s["logistic"]),
                     f"{_pct(s['spreading_cov'][0])} / "
                     f"{_pct(s['logistic_cov'][0])}"])
    return rows


def _then_rows(res: dict) -> list:
    rows = [["clustering", "clicks", "accuracy", "coverage at 90 %"]]
    for method, c in res.get("cluster_then_label", {}).items():
        rows.append([method.replace("_over", " (k=10)"), str(c["clicks"]),
                     _pct(c.get("accuracy")), _pct(c.get("coverage_90"))])
    return rows


def write_pdf(path, name: str, review, chips: np.ndarray, X: np.ndarray,
              results: dict, embedding: str):
    """Write the report for ``embedding``'s pictures; ``chips`` and ``X``
    are indexed like ``review.records``."""
    res = results[embedding]
    idx = np.array(res["index"])
    y = np.array(res["y"])
    names = list(res["classes"])
    records = [review.records[i] for i in idx]
    Xe = X[idx]
    predicted = np.array([r.predicted for r in records])
    colours = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(names)}
    if IGNORE in colours:
        colours[IGNORE] = GREY
    is_new = np.array([review.is_new(r.truth) for r in records])
    known = np.array([r.truth != IGNORE and not review.is_new(r.truth)
                      for r in records])

    pdf = Report(name)
    pdf.add_page()
    pdf.h1(f"Error partitioning: {name}")
    pdf.para(review.summary())
    pdf.para(f"Pictures from the {embedding} embedding; the first table "
             f"compares every embedding run. {datetime.now():%Y-%m-%d %H:%M}."
             " Percentages throughout; +/- is one standard deviation over "
             "random draws. Under every chip: its verdict, and a bar in the "
             "verdict's colour.")
    pdf.legend(colours)
    pdf.ln(1)
    pdf.h2("The embeddings compared")
    pdf.rows(_comparison(results), (18, 22, 20, 26, 16))
    pdf.h2(f"Clustering by predicted class, over-clustered ({embedding})")
    pdf.para("Purity: the accuracy if each cluster took its majority verdict. "
             "Clicks: the verdicts that costs. An HDBSCAN chip left "
             "unclustered is a click of its own.")
    pdf.rows(_clustering_rows(res), (18, 10, 10, 8, 14, 10, 14, 10, 14, 18))
    pdf.h2(f"Seeded labelling ({embedding})")
    pdf.para("A few verdicts per class, the rest filled in. Accuracy on the "
             "chips not seeded; coverage is the share of them that can be "
             "taken at 90 % precision by trusting only the confident ones.")
    pdf.rows(_seeded_rows(res), (18, 18, 18, 18, 34))
    pdf.h2(f"Cluster, then label ({embedding})")
    pdf.para("The reviewer names the most central chip of each cluster and "
             "the names spread to the rest.")
    pdf.rows(_then_rows(res), (24, 14, 14, 20))
    novelty = res.get("novelty") or {}
    if novelty:
        pdf.h2(f"Novelty ({embedding})")
        pdf.para(f"AUROC for telling a chip of a class the model does not "
                 f"know ({novelty['new']} chips) from one of a known class "
                 f"({novelty['known']}), by distance to the nearest known "
                 f"prototype {_pct(novelty['prototype_auroc'])} and to the "
                 f"nearest known seeds {_pct(novelty['neighbour_auroc'])}. "
                 "50 is chance, 100 perfect.")

    # -- the clusters, group by group ----------------------------------------
    for group, g in res["groups"].items():
        if g.get("skipped") or "assignments" not in g:
            continue
        mask = np.ones(len(y), dtype=bool) if group == "all" \
            else predicted == group
        rows_in = np.flatnonzero(mask)
        labels = np.array(g["assignments"]["kmeans"])
        m = g["methods"]["kmeans@over_k"]
        pdf.add_page()
        title = ("All chips" if group == "all"
                 else f"Predicted {group}")
        pdf.h1(f"{title}: {len(rows_in)} chips in {g['k_over']} clusters "
               f"(k-means)")
        kinds = ", ".join(f"{k} {n}" for k, n in g["kinds"].items())
        pdf.para(f"Verdicts: {kinds}. Purity {_pct(m['purity'])} for "
                 f"{m['clicks']} clicks. Each cluster's chips in order of "
                 "distance from its centre; a red frame marks a chip whose "
                 "verdict is not the cluster's majority.")
        sizes = [(int((labels == c).sum()), c) for c in np.unique(labels)]
        for n, c in sorted(sizes, reverse=True):
            members = rows_in[labels == c]
            centre = Xe[members].mean(axis=0)
            order = np.argsort(((Xe[members] - centre) ** 2).sum(axis=1))
            members = members[order]
            counts = np.bincount(y[members], minlength=len(names))
            majority = int(counts.argmax())
            pdf.h2(f"Cluster {c + 1}: {n} chips, majority {names[majority]} "
                   f"({counts[majority]} of {n})", keep=ROW_H + 9)
            shown = members[:MAX_PER_CLUSTER]
            pdf.sheet([chips[idx[i]] for i in shown],
                      [names[y[i]] for i in shown],
                      [colours[names[y[i]]] for i in shown],
                      [None if y[i] == majority else RED for i in shown],
                      more=len(members) - len(shown))

    # -- one seeded fill-in ---------------------------------------------------
    rng = np.random.default_rng(0)
    seeds = methods.seed_split(y, SEEDS_SHOWN, rng)
    if seeds.sum() and len(np.unique(y[seeds])) >= 2 and (~seeds).sum():
        model = methods.spread(Xe, np.where(seeds, y, -1),
                               max(2, min(10, len(y) - 1)))
        rest = np.flatnonzero(~seeds)
        dist = model.label_distributions_[rest]
        guess = model.classes_[dist.argmax(axis=1)]
        wrong = rest[guess != y[rest]]
        pdf.add_page()
        pdf.h1(f"{SEEDS_SHOWN} verdicts per class, the rest spread")
        pdf.para(f"{int(seeds.sum())} seeds; {len(rest)} chips filled in by "
                 f"label spreading, {len(rest) - len(wrong)} right "
                 f"({_pct(1 - len(wrong) / max(1, len(rest)))}), "
                 f"{len(wrong)} wrong. One random draw; the table on the "
                 "cover averages ten.")
        pdf.h2("The seeds", keep=ROW_H + 9)
        seed_rows = [i for c in range(len(names))
                     for i in np.flatnonzero(seeds & (y == c))]
        pdf.sheet([chips[idx[i]] for i in seed_rows],
                  [names[y[i]] for i in seed_rows],
                  [colours[names[y[i]]] for i in seed_rows],
                  [None] * len(seed_rows))
        if len(wrong):
            pdf.h2(f"Filled in wrongly ({len(wrong)}): captioned with what "
                   "spreading called it; the bar is the verdict's colour",
                   keep=ROW_H + 9)
            confidence = dist.max(axis=1)[guess != y[rest]]
            order = np.argsort(-confidence)
            shown = wrong[order][:MAX_SHEET]
            called = {int(i): names[int(c)] for i, c in zip(wrong, guess[guess != y[rest]])}
            pdf.sheet([chips[idx[i]] for i in shown],
                      [called[int(i)] for i in shown],
                      [colours[names[y[i]]] for i in shown],
                      [RED] * len(shown), more=len(wrong) - len(shown))

    # -- farthest from the known classes ---------------------------------------
    if known.sum() and (is_new.sum() or (~known).sum()):
        known_seeds = np.zeros(len(y), dtype=bool)
        for c in np.unique(y[known]):
            members = np.flatnonzero(known & (y == c))
            take = min(5, len(members) - 1)
            if take > 0:
                known_seeds[rng.choice(members, take, replace=False)] = True
        if known_seeds.sum():
            prototypes = np.stack([Xe[known_seeds & (y == c)].mean(axis=0)
                                   for c in np.unique(y[known_seeds])])
            rest = np.flatnonzero(~known_seeds)
            distance = 1.0 - (Xe[rest] @ prototypes.T).max(axis=1)
            shown = rest[np.argsort(-distance)][:MAX_SHEET]
            pdf.add_page()
            pdf.h1("Farthest from the known classes")
            pdf.para("The chips least like any known-class prototype (five "
                     "seeds per known class), farthest first: what a "
                     "reviewer should see first when a new class may be "
                     "hiding in a review. An orange frame marks a chip whose "
                     "verdict is a class the model does not predict; a grey "
                     "bar, Ignore.")
            pdf.sheet([chips[idx[i]] for i in shown],
                      [names[y[i]] for i in shown],
                      [colours[names[y[i]]] for i in shown],
                      [ORANGE if is_new[i] else None for i in shown])
    pdf.output(str(path))
