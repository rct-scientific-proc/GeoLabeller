# Error partitioning: can the mistakes of a model sort themselves?

Model Review hands a reviewer the chips a model got confidently wrong,
grouped by what the model called them, and asks for a verdict on each:
the right class, a class nobody has named yet, or Ignore. Giving those
verdicts one chip at a time is the tedious part. This harness scores
techniques from the literature that would take most of the clicks away,
against verdicts a project already holds, so a technique proves itself
on real reviews before anything reaches the GUI.

Nothing here ships. The folder is outside `app/`, the frozen build never
sees it, and it needs PyTorch (`requirements-ml.txt`) and scikit-learn
(`requirements-research.txt`).

## Running it

On a real review - a project whose ledger holds the verdicts, and the
JSON the ML team gave:

    python -m research.partition.run --project P.geolabel --review R.json

On the synthetic benchmark, which builds its own imagery, project and
review with known truth:

    python -m research.partition.run --synthetic

Options: `--chip-size` (source pixels read around a chip; 224 is the
review window's default), `--embedding pixels,resnet18,dinov2`,
`--no-tta` (one view per chip instead of eight), `--repeats` (random
draws per seeded score), `--results` and `--cache` (where the report
and the cached chips go; the defaults are `results/` and `cache/`
here). A run writes three files: the report as Markdown and as JSON
(every number, plus the cluster each chip landed in), and a PDF with
the chips themselves - `--pdf-embedding` picks whose pictures it
shows, `--no-pdf` skips it.

Only chips with a verdict are scored. A chip the ledger does not know is
carried but not counted, so the first run on a real review should be on
one the reviewer finished.

## The techniques

**Embeddings.** Everything runs on a vector per chip: a 16x16 grey
thumbnail through PCA (the floor), ImageNet ResNet18 features (what
Train on verdicts puts a head on), and DINOv2 ViT-S/14 (Oquab et al.
2023), the self-supervised features the category-discovery and
slice-discovery papers cluster on. The networks see the eight turned and
mirrored views of a chip and the features are averaged, since overhead
imagery has no up.

**Clustering: one verdict per cluster.** k-means; spectral clustering on
a k-nearest-neighbour graph (Shi and Malik 2000; Ng, Jordan and Weiss
2001; von Luxburg 2007 for the tutorial); agglomerative with Ward
linkage; HDBSCAN (Campello, Moulavi and Sander 2013), which leaves
outliers unclustered instead of forcing them in. Scored within each
predicted class (the grid the reviewer sees) and over all chips, by
adjusted Rand index, normalised mutual information and purity: the
accuracy if the reviewer gave each cluster its majority verdict, and the
clicks that takes. Each method runs at the true number of kinds and at
one chosen by silhouette, which is all a reviewer would have.

**Seeded labelling.** A few verdicts per class, the rest filled in:
label spreading over the kNN graph (Zhou et al. 2004, "Learning with
Local and Global Consistency"; Zhu and Ghahramani 2002 for label
propagation), a kNN vote, and a logistic-regression head on the frozen
features, which is what Train on verdicts does. Scored by accuracy on
the unseeded chips and by coverage at 90 % precision: the share of them
that could be filled in automatically by taking only the confident ones.

**Cluster, then label.** The workflow the two combine into: cluster,
the reviewer names the most central chip of each cluster, the names
spread to the rest. Clicks equals clusters.

**Novelty.** For a chip of a class the model does not know, the distance
to the nearest known-class prototype and to the nearest known seeds,
scored by AUROC. Generalized Category Discovery (Vaze et al. 2022) rests
on the same distances; the confident-learning line (Northcutt et al.
2021) is what produced the review file in the first place and has
nothing further to say about which chips are new.

## The PDF

The PDF is the report to read with a reviewer. A cover compares the
embeddings and gives the tables for the one shown. Then, for each
predicted class and for all chips together, the ten k-means clusters
as contact sheets: every chip captioned with its verdict and
underlined in the verdict's colour, ordered from the cluster's centre
outward, a red frame on a chip whose verdict is not the cluster's
majority. A page shows one seeded fill-in: the three seeds per class
and the chips label spreading got wrong, captioned with what it
called them. The last page shows the chips farthest from every known
class - what a reviewer should look at first when a new class may be
hiding - with an orange frame on those whose verdict is a class the
model never predicts.

## Reading a report

The questions it answers, in order: does clustering by predicted class
give clusters pure enough to label at a glance, and at how many clicks;
how many seeds per class before propagation fills in the rest reliably,
and how much of the rest can be taken at 90 % precision; whether
clustering first beats seeding at random for the same clicks; and
whether distance flags the new-class chips. The embedding columns answer
which features to compute in the application.
