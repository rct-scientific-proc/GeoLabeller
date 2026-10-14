"""The techniques, scored against the verdicts.

Every function takes unit-length embeddings ``X`` (N, D) and integer
truths ``y`` (N,), and returns plain dicts for the report.

Clustering (one verdict per cluster instead of per chip):
  k-means; spectral clustering on a k-nearest-neighbour graph (Shi and
  Malik 2000; Ng, Jordan and Weiss 2001); agglomerative (Ward); HDBSCAN
  (Campello et al. 2013), which leaves outliers unclustered rather than
  forcing them in.  Scored by adjusted Rand index, normalised mutual
  information, and purity: the accuracy if the reviewer gave each
  cluster its majority verdict - and the clicks that takes.

Seeded labelling (a few verdicts per class, the rest filled in):
  label spreading over the kNN graph (Zhou et al. 2004); a kNN vote; a
  logistic-regression head on the frozen features (what Train on
  verdicts does).  Scored by accuracy on the unlabelled chips, and by the
  share of them that can be filled in at 90 % precision when only the
  confident ones are taken.

Cluster, then label (the workflow the two combine into):
  cluster, the reviewer names each cluster's most central chip, and the
  names spread to the rest.  Clicks = clusters.

Novelty (chips of a class the model does not know):
  distance to the nearest class prototype and to the nearest seeds of
  the known classes, scored by AUROC against "the verdict names a class
  the model never predicts" (Vaze et al. 2022 use the same prototype
  distances to decide what is new).
"""
from __future__ import annotations

import warnings

import numpy as np
from sklearn.cluster import HDBSCAN, AgglomerativeClustering, KMeans, \
    SpectralClustering
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import adjusted_rand_score, \
    normalized_mutual_info_score, roc_auc_score, silhouette_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.semi_supervised import LabelSpreading

SEEDS = (1, 2, 3, 5, 10)
OVER_K = 10                     # clusters, when over-clustering
PRECISION = 0.90


# -- clustering -----------------------------------------------------------

def purity(labels: np.ndarray, y: np.ndarray) -> tuple:
    """(accuracy, clicks) if every cluster takes its majority truth and
    every unclustered chip (label -1) is looked at on its own."""
    right = 0
    clicks = 0
    for label in np.unique(labels):
        members = y[labels == label]
        if label == -1:
            right += len(members)
            clicks += len(members)
            continue
        right += int(np.bincount(members).max())
        clicks += 1
    return right / max(1, len(y)), clicks


def _cluster(method: str, X: np.ndarray, k: int, rng: int = 0) -> np.ndarray:
    n = len(X)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if method == "kmeans":
            return KMeans(k, n_init=10, random_state=rng).fit_predict(X)
        if method == "spectral":
            return SpectralClustering(
                k, affinity="nearest_neighbors",
                n_neighbors=max(2, min(10, n - 1)), assign_labels="cluster_qr",
                random_state=rng).fit_predict(X)
        if method == "agglomerative":
            return AgglomerativeClustering(k, linkage="ward").fit_predict(X)
        if method == "hdbscan":
            return HDBSCAN(min_cluster_size=max(3, min(5, n // 10)),
                           min_samples=3).fit_predict(X)
    raise ValueError(method)


def choose_k(X: np.ndarray, k_max: int = 10) -> int:
    """The k with the best silhouette under k-means, 2..k_max (the
    reviewer does not know how many kinds a group holds)."""
    best, best_score = 2, -1.0
    for k in range(2, min(k_max, len(X) - 1) + 1):
        labels = _cluster("kmeans", X, k)
        if len(np.unique(labels)) < 2:
            continue
        score = silhouette_score(X, labels)
        if score > best_score:
            best, best_score = k, score
    return best


def over_k(n: int, clusters: int = OVER_K) -> int:
    """Over-clustering: more clusters than kinds, so each is purer and
    the reviewer still gives only a handful of verdicts - what a tool
    would do, not knowing how many kinds a group holds."""
    return max(2, min(clusters, n // 4))


def cluster_scores(X: np.ndarray, y: np.ndarray) -> dict:
    """Every method on one group: at the true number of kinds, at a
    silhouette-chosen one, and over-clustered."""
    k_true = len(np.unique(y))
    k_est = choose_k(X)
    k_over = over_k(len(y))
    out = {"n": int(len(y)), "k_true": int(k_true), "k_estimated": int(k_est),
           "k_over": int(k_over), "methods": {}}
    for method in ("kmeans", "spectral", "agglomerative"):
        for k, tag in ((k_true, "true_k"), (k_est, "est_k"),
                       (k_over, "over_k")):
            labels = _cluster(method, X, k)
            acc, clicks = purity(labels, y)
            out["methods"][f"{method}@{tag}"] = {
                "ari": float(adjusted_rand_score(y, labels)),
                "nmi": float(normalized_mutual_info_score(y, labels)),
                "purity": float(acc), "clicks": int(clicks),
                "clusters": int(len(np.unique(labels)))}
    labels = _cluster("hdbscan", X, 0)
    acc, clicks = purity(labels, y)
    out["methods"]["hdbscan"] = {
        "ari": float(adjusted_rand_score(y, labels)),
        "nmi": float(normalized_mutual_info_score(y, labels)),
        "purity": float(acc), "clicks": int(clicks),
        "clusters": int(len(np.unique(labels[labels >= 0]))),
        "unclustered": int((labels == -1).sum())}
    return out


# -- seeded labelling -------------------------------------------------------

def _seed_split(y: np.ndarray, per_class: int, rng) -> np.ndarray:
    """A boolean mask of the seeds: up to ``per_class`` of each class,
    always leaving at least one of the class unlabelled."""
    seeds = np.zeros(len(y), dtype=bool)
    for c in np.unique(y):
        members = np.flatnonzero(y == c)
        take = min(per_class, len(members) - 1)
        if take > 0:
            seeds[rng.choice(members, take, replace=False)] = True
    return seeds


def coverage_at_precision(confidence: np.ndarray, right: np.ndarray,
                          precision: float = PRECISION) -> float:
    """The largest share of chips that can be taken, most confident
    first, with at least ``precision`` of them right."""
    if len(right) == 0:
        return 0.0
    order = np.argsort(-confidence)
    hits = np.cumsum(right[order])
    counts = np.arange(1, len(order) + 1)
    good = np.flatnonzero(hits / counts >= precision)
    return float(counts[good[-1]] / len(order)) if len(good) else 0.0


def _spread(X, y_seeded, n_neighbors):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = LabelSpreading(kernel="knn", n_neighbors=n_neighbors,
                               alpha=0.2, max_iter=200)
        model.fit(X, y_seeded)
    return model


def seeded_scores(X: np.ndarray, y: np.ndarray, seeds=SEEDS,
                  repeats: int = 10, seed: int = 0) -> dict:
    """Accuracy and 90 %-precision coverage on the unlabelled chips, per
    number of seeds per class, averaged over random draws."""
    rng = np.random.default_rng(seed)
    n_neighbors = max(2, min(10, len(y) - 1))
    out = {}
    for per_class in seeds:
        rows: dict = {"spreading": [], "knn": [], "logistic": [],
                      "spreading_cov": [], "logistic_cov": []}
        for _ in range(repeats):
            mask = _seed_split(y, per_class, rng)
            rest = ~mask
            if rest.sum() == 0 or len(np.unique(y[mask])) < 2:
                continue
            seeded = np.where(mask, y, -1)
            model = _spread(X, seeded, n_neighbors)
            dist = model.label_distributions_[rest]
            guess = model.classes_[dist.argmax(axis=1)]
            right = guess == y[rest]
            rows["spreading"].append(right.mean())
            rows["spreading_cov"].append(
                coverage_at_precision(dist.max(axis=1), right))
            knn = KNeighborsClassifier(
                n_neighbors=max(1, min(5, mask.sum())), metric="cosine")
            knn.fit(X[mask], y[mask])
            rows["knn"].append((knn.predict(X[rest]) == y[rest]).mean())
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                head = LogisticRegression(C=1.0, max_iter=1000)
                head.fit(X[mask], y[mask])
            prob = head.predict_proba(X[rest])
            guess = head.classes_[prob.argmax(axis=1)]
            right = guess == y[rest]
            rows["logistic"].append(right.mean())
            rows["logistic_cov"].append(
                coverage_at_precision(prob.max(axis=1), right))
        if rows["spreading"]:
            out[per_class] = {key: (float(np.mean(v)), float(np.std(v)))
                              for key, v in rows.items()}
    return out


# -- cluster, then label ----------------------------------------------------

def cluster_then_label(X: np.ndarray, y: np.ndarray) -> dict:
    """The reviewer names the most central chip of each cluster; the
    names spread to everything else. Accuracy on the chips not named,
    and the clicks it took."""
    n_neighbors = max(2, min(10, len(y) - 1))
    out = {}
    k_est = choose_k(X)
    k_over = over_k(len(y))
    for method, k in (("kmeans", k_est), ("spectral", k_est),
                      ("kmeans_over", k_over), ("spectral_over", k_over),
                      ("hdbscan", 0)):
        labels = _cluster(method.replace("_over", ""), X, k)
        seeds = np.zeros(len(y), dtype=bool)
        for label in np.unique(labels):
            members = np.flatnonzero(labels == label)
            if label == -1:
                continue
            centre = X[members].mean(axis=0)
            seeds[members[np.argmin(((X[members] - centre) ** 2).sum(1))]] = True
        if seeds.sum() == 0 or len(np.unique(y[seeds])) < 2:
            out[method] = {"clicks": int(seeds.sum()), "accuracy": None}
            continue
        model = _spread(X, np.where(seeds, y, -1), n_neighbors)
        rest = ~seeds
        dist = model.label_distributions_[rest]
        guess = model.classes_[dist.argmax(axis=1)]
        right = guess == y[rest]
        out[method] = {"clicks": int(seeds.sum()),
                       "accuracy": float(right.mean()),
                       "coverage_90": coverage_at_precision(dist.max(axis=1),
                                                            right)}
    return out


# -- novelty -----------------------------------------------------------------

def novelty_scores(X: np.ndarray, y: np.ndarray, is_new: np.ndarray,
                   known: np.ndarray, per_class: int = 5, repeats: int = 10,
                   seed: int = 0) -> dict:
    """AUROC of two distances for telling a chip of a class the model
    does not know from one it does, given ``per_class`` seeds of each
    known class. ``known`` marks chips whose truth is a known class."""
    if is_new.sum() == 0 or known.sum() == 0:
        return {}
    rng = np.random.default_rng(seed)
    prototype_auc, neighbour_auc = [], []
    for _ in range(repeats):
        seeds = np.zeros(len(y), dtype=bool)
        for c in np.unique(y[known]):
            members = np.flatnonzero(known & (y == c))
            take = min(per_class, len(members) - 1)
            if take > 0:
                seeds[rng.choice(members, take, replace=False)] = True
        if seeds.sum() == 0:
            continue
        rest = ~seeds
        prototypes = np.stack([X[seeds & (y == c)].mean(axis=0)
                               for c in np.unique(y[seeds])])
        to_prototype = 1.0 - (X[rest] @ prototypes.T).max(axis=1)
        sims = X[rest] @ X[seeds].T
        k = min(3, seeds.sum())
        to_neighbours = 1.0 - np.sort(sims, axis=1)[:, -k:].mean(axis=1)
        target = is_new[rest]
        if target.sum() == 0 or (~target).sum() == 0:
            continue
        prototype_auc.append(roc_auc_score(target, to_prototype))
        neighbour_auc.append(roc_auc_score(target, to_neighbours))
    if not prototype_auc:
        return {}
    return {"prototype_auroc": (float(np.mean(prototype_auc)),
                                float(np.std(prototype_auc))),
            "neighbour_auroc": (float(np.mean(neighbour_auc)),
                                float(np.std(neighbour_auc))),
            "new": int(is_new.sum()), "known": int(known.sum())}
