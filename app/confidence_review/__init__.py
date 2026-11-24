"""Suggested confidence ratings (2.11.0, asked for 2026-11-24).

"If PyTorch is detected, allow users to train on the individual classes
based on confidence values, so that the app can auto assign what it thinks
the confidence is for remaining objects that have not had a confidence
applied."

Ryan's decisions: the model only SUGGESTS, in a large grid of its own where
a person corrects many snippets quickly and then trains again or accepts
(like Model Review's training); a suggestion never reaches an H5 export -
only a rating a person accepted does, so suggestions live in the window and
never in the project; a class is learned from 30 rated labels at least, and
a rating only from 5 examples of it; every view is rated from its own
snippet, always.

    ratings.py   the rules, and the training - Qt-free; PyTorch through
                 model_review.learner, a model per class
    window.py    SuggestWindow: Train, the grid, corrections, Accept

The main window opens the window (Labels > Suggest Confidence Ratings...,
or the Snippet Editor's Confidence section) and writes what is accepted,
as one undo step.
"""
