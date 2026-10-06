"""Model Review: the model team's mistakes, looked at and labelled.

Asked for 2026-10-05. The team runs their classifier over imagery that is
in a project and writes out the chips it was confidently wrong about:
which file, where, what it called the thing, how sure it was. Every one
is clutter the model took for a real class - nothing in the file is a
label the project already has. A labeller opens the file in this window,
sees the chips the model called "vessel" (say) from most to least
confident, and gives each its real class - usually a new one, "rock" or
"wake", so the model can learn the clutter - or Ignore. One click imports
the verdicts into the project as labels. Then the team retrains, runs
again, and the next file's chips come in; the ones already reviewed stay
out of the way. The loop is the point, so the project remembers every
verdict (format 5.0, "model_review").

    source.py    the team's JSON, read and matched onto the project's
                 images (by file name, any directory; positions scaled
                 from the size the file states to the image here)
    ledger.py    the memory: what the model called each chip and what
                 it became, kept in the project; "already reviewed"
    importer.py  the verdicts into the project: labels with lon/lat,
                 attribution and a description saying what the model
                 said, plus the ledger entries - one undo step
    window.py    the window: the grid of chips, one predicted class at
                 a time, the New label box, the selection, Import

Nothing here imports the main window; the main window opens the window,
answers its signals, and owns the project.
"""
