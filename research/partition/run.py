"""Run every technique on a review and write the report.

    python -m research.partition.run --synthetic
    python -m research.partition.run --project P.geolabel --review R.json \
        [--chip-size 224] [--embedding pixels,resnet18,dinov2] [--no-tta]

The report (Markdown and JSON) lands in research/partition/results/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# The first app import runs app/__init__ (the C++ runtime preload),
# before anything imports torch or Qt.
from app.model_review.ledger import IGNORE
from research.partition import benchmark, embed, methods, report_pdf
from research.partition.data import ReviewSet, load_review

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
CACHE = HERE / "cache"
MIN_GROUP = 12                  # chips a predicted class needs to be scored


def log(text: str):
    print(text, flush=True)


def encode(truths) -> tuple:
    truths = list(truths)
    names = sorted(set(truths))
    index = {name: i for i, name in enumerate(names)}
    return np.array([index[t] for t in truths]), names


def evaluate(review: ReviewSet, X_all: np.ndarray, ok: np.ndarray,
             repeats: int) -> dict:
    """Every technique on the reviewed, readable chips: clustering per
    predicted class and over all; seeded labelling, cluster-then-label
    and novelty over all."""
    rows = [(i, r) for i, r in enumerate(review.records)
            if r.truth is not None and ok[i]]
    if len(rows) < MIN_GROUP:
        return {"error": f"only {len(rows)} reviewed, readable chips"}
    idx = np.array([i for i, _r in rows])
    records = [r for _i, r in rows]
    X = X_all[idx]
    y, names = encode(r.truth for r in records)
    predicted = np.array([r.predicted for r in records])
    out: dict = {"chips": int(len(records)), "classes": names,
                 "index": idx.tolist(), "y": y.tolist(), "groups": {}}
    for group in sorted(set(predicted)) + ["all"]:
        mask = np.ones(len(y), dtype=bool) if group == "all" \
            else predicted == group
        if mask.sum() < MIN_GROUP or len(np.unique(y[mask])) < 2:
            out["groups"][group] = {"n": int(mask.sum()), "skipped": True}
            continue
        log(f"  clustering: {group} ({int(mask.sum())} chips, "
            f"{len(np.unique(y[mask]))} kinds)")
        scores = methods.cluster_scores(X[mask], y[mask])
        scores["kinds"] = {names[c]: int((y[mask] == c).sum())
                           for c in np.unique(y[mask])}
        out["groups"][group] = scores
    log("  seeded labelling")
    out["seeded"] = methods.seeded_scores(X, y, repeats=repeats)
    log("  cluster, then label")
    out["cluster_then_label"] = methods.cluster_then_label(X, y)
    is_new = np.array([review.is_new(r.truth) for r in records])
    known = np.array([r.truth != IGNORE and not review.is_new(r.truth)
                      for r in records])
    log("  novelty")
    out["novelty"] = methods.novelty_scores(X, y, is_new, known,
                                            repeats=repeats)
    out["novelty_with_ignore"] = methods.novelty_scores(
        X, y, is_new | np.array([r.truth == IGNORE for r in records]),
        known, repeats=repeats)
    return out


# -- the report ---------------------------------------------------------------

def _pct(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, tuple):
        return f"{100 * value[0]:.0f} +/- {100 * value[1]:.0f}"
    return f"{100 * value:.0f}"


def render(name: str, summary: str, results: dict, elapsed: float) -> str:
    lines = [f"# Error partitioning: {name}", "",
             f"{summary}", "",
             f"Run {datetime.now():%Y-%m-%d %H:%M}, {elapsed:.0f} s. "
             "Percentages throughout; +/- is one standard deviation over "
             "random draws.", ""]
    embeddings = list(results)
    first = next((r for r in results.values() if "error" not in r), None)
    if first is None:
        lines.append("Nothing could be scored: "
                     + "; ".join(f"{e}: {r.get('error')}"
                                 for e, r in results.items()))
        return "\n".join(lines)
    lines += ["## Clustering: one verdict per cluster", "",
              "Purity is the accuracy if each cluster took its majority "
              "verdict; clicks is how many verdicts that is (an "
              "unclustered HDBSCAN chip is a click of its own). The true "
              "number of kinds is what the verdicts say; the estimated one "
              "is chosen by silhouette, as it would have to be in use.", ""]
    for group, scores in first["groups"].items():
        if scores.get("skipped"):
            lines.append(f"- **{group}**: {scores['n']} chips, too few or "
                         "of one kind; skipped.")
            continue
        kinds = ", ".join(f"{k} {n}" for k, n in scores["kinds"].items())
        lines += ["", f"### Predicted {group}: {scores['n']} chips "
                  f"({kinds}); {scores['k_true']} kinds, "
                  f"{scores['k_estimated']} estimated, "
                  f"{scores['k_over']} over-clustered", "",
                  "| method | " + " | ".join(
                      f"{e} ARI | {e} purity | {e} clicks" for e in embeddings)
                  + " |",
                  "|---|" + "---|" * (3 * len(embeddings))]
        for method in first["groups"][group]["methods"]:
            cells = []
            for e in embeddings:
                m = results[e].get("groups", {}).get(group, {}).get(
                    "methods", {}).get(method)
                if m is None:
                    cells += ["-", "-", "-"]
                else:
                    extra = (f" ({m['unclustered']} alone)"
                             if "unclustered" in m else "")
                    cells += [_pct(m["ari"]), _pct(m["purity"]),
                              f"{m['clicks']}{extra}"]
            lines.append(f"| {method} | " + " | ".join(cells) + " |")
    lines += ["", "## Seeded labelling: a few verdicts per class, the rest "
              "filled in", "",
              "Accuracy on the chips not seeded. Coverage is the share of "
              f"them that can be filled in at {methods.PRECISION:.0%} "
              "precision by taking only the confident ones.", "",
              "| seeds/class | " + " | ".join(
                  f"{e} spreading | {e} kNN | {e} logistic | {e} cov. "
                  "(spread / logistic)" for e in embeddings) + " |",
              "|---|" + "---|" * (4 * len(embeddings))]
    for per_class in methods.SEEDS:
        cells = []
        for e in embeddings:
            s = results[e].get("seeded", {}).get(per_class)
            if s is None:
                cells += ["-"] * 4
            else:
                cells += [_pct(s["spreading"]), _pct(s["knn"]),
                          _pct(s["logistic"]),
                          f"{_pct(s['spreading_cov'][0])} / "
                          f"{_pct(s['logistic_cov'][0])}"]
        lines.append(f"| {per_class} | " + " | ".join(cells) + " |")
    lines += ["", "## Cluster, then label", "",
              "The reviewer names the most central chip of each cluster "
              "(clicks) and the names spread to the rest.", "",
              "| method | " + " | ".join(
                  f"{e} clicks | {e} accuracy | {e} cov." for e in embeddings)
              + " |",
              "|---|" + "---|" * (3 * len(embeddings))]
    for method in ("kmeans", "spectral", "kmeans_over", "spectral_over",
                   "hdbscan"):
        cells = []
        for e in embeddings:
            c = results[e].get("cluster_then_label", {}).get(method)
            if c is None:
                cells += ["-"] * 3
            else:
                cells += [str(c["clicks"]), _pct(c["accuracy"]),
                          _pct(c.get("coverage_90"))]
        lines.append(f"| {method} | " + " | ".join(cells) + " |")
    lines += ["", "## Novelty: chips of a class the model does not know", "",
              "AUROC of a distance for telling a new-class chip from a "
              "known-class one, given 5 seeds of each known class. 50 is "
              "chance, 100 perfect. The second row counts Ignore chips as "
              "new as well.", "",
              "| set | " + " | ".join(f"{e} prototype | {e} neighbours"
                                      for e in embeddings) + " |",
              "|---|" + "---|" * (2 * len(embeddings))]
    for key, label in (("novelty", "new classes"),
                       ("novelty_with_ignore", "new classes + Ignore")):
        cells = []
        for e in embeddings:
            n = results[e].get(key) or {}
            cells += [_pct(n.get("prototype_auroc")),
                      _pct(n.get("neighbour_auroc"))]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# -- main -------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--synthetic", action="store_true",
                        help="build and score the synthetic benchmark")
    parser.add_argument("--project", help="a .geolabel project")
    parser.add_argument("--review", help="the review JSON the ML team gave")
    parser.add_argument("--chip-size", type=int, default=224,
                        help="source pixels read around a chip (224, "
                             "the review window's default)")
    parser.add_argument("--embedding", default=",".join(embed.EMBEDDINGS),
                        help="comma-separated: pixels, resnet18, dinov2")
    parser.add_argument("--no-tta", action="store_true",
                        help="one view per chip instead of eight")
    parser.add_argument("--repeats", type=int, default=10,
                        help="random draws per seeded score")
    parser.add_argument("--name", help="the report's name")
    parser.add_argument("--no-pdf", action="store_true",
                        help="skip the PDF with the chips")
    parser.add_argument("--pdf-embedding",
                        help="whose pictures the PDF shows (default: "
                             "dinov2, else resnet18, else pixels)")
    parser.add_argument("--out", help="where the benchmark is built "
                                      "(synthetic only)")
    parser.add_argument("--results", default=str(RESULTS),
                        help="where the report is written")
    parser.add_argument("--cache", default=str(CACHE),
                        help="where chips and embeddings are cached")
    args = parser.parse_args(argv)
    results_dir, cache_dir = Path(args.results), Path(args.cache)
    started = time.time()
    if args.synthetic:
        out_dir = Path(args.out) if args.out else cache_dir / "synthetic"
        log(f"building the synthetic benchmark in {out_dir}")
        built = benchmark.build(out_dir)
        args.project, args.review = built["project"], built["review"]
        args.chip_size = built["chip_size"]
        name = args.name or "synthetic"
    elif args.project and args.review:
        name = args.name or Path(args.review).stem
    else:
        parser.error("--synthetic, or --project and --review")
    review, report = load_review(args.project, args.review)
    log(f"{name}: {review.summary()}")
    log(f"matched {report.matched} of {report.total}; no image "
        f"{sum(report.no_image.values())}, unknown class "
        f"{sum(report.unknown_class.values())}, unreadable "
        f"{sum(report.unreadable.values())}")
    places = [r.place for r in review.records]
    chips, ok = embed.read_chips(places, args.chip_size, 128, cache_dir, log)
    log(f"{int(ok.sum())} of {len(ok)} chips read")
    results: dict = {}
    vectors: dict = {}
    key = embed.cache_key(places, args.chip_size, 128)
    for embedding in [e.strip() for e in args.embedding.split(",") if e]:
        log(f"embedding: {embedding}")
        try:
            X = embed.cached(cache_dir / f"X-{key}-{embedding}"
                             f"{'' if args.no_tta else '-tta'}.npy",
                             lambda: embed.embed(embedding, chips,
                                                 tta=not args.no_tta,
                                                 log=log))
        except Exception as exc:      # noqa: BLE001 - one embedding
            log(f"  {embedding} failed: {type(exc).__name__}: {exc}")
            results[embedding] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        vectors[embedding] = X
        results[embedding] = evaluate(review, X, ok, args.repeats)
    text = render(name, review.summary(), results, time.time() - started)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (results_dir / f"{stamp}-{name}.md").write_text(text, encoding="utf-8")
    (results_dir / f"{stamp}-{name}.json").write_text(
        json.dumps(results, indent=1, default=str), encoding="utf-8")
    log("")
    log(text)
    log(f"written: {results_dir / f'{stamp}-{name}.md'}")
    if not args.no_pdf:
        shown = args.pdf_embedding or next(
            (e for e in ("dinov2", "resnet18", "pixels")
             if "error" not in results.get(e, {"error": 1})), None)
        if shown in vectors and "error" not in results[shown]:
            pdf_path = results_dir / f"{stamp}-{name}.pdf"
            report_pdf.write_pdf(pdf_path, name, review, chips,
                                 vectors[shown], results, shown)
            log(f"written: {pdf_path}")
        else:
            log("no PDF: no embedding to show")
    return 0


if __name__ == "__main__":
    sys.exit(main())
