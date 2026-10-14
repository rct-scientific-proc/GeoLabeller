"""Automatic partitioning of a model's mistakes - research harness.

Not part of the application or the frozen build. Scores techniques from
the literature (clustering, label propagation, novelty scores over
image embeddings) against the verdicts a project already holds, so a
technique's worth is measured before anything reaches the GUI.

    python -m research.partition.run --synthetic
    python -m research.partition.run --project P.geolabel --review R.json
"""
