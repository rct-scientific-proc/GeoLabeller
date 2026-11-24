# GeoLabeller release notes

Newest first. Each release's notes are the message of its release tag on GitHub.

## 2.11.0 (2026-11-24)

Suggested confidence ratings. Where PyTorch is installed (a run from
source with requirements-ml.txt; the installed GeoLabeller leaves it
out), GeoLabeller can learn from the confidence ratings people gave and
suggest a rating for every label without one. Open it with Labels >
Suggest Confidence Ratings..., or Suggest ratings... in the Snippet
Editor's Confidence section, which is greyed, saying why, without
PyTorch.

Train learns a model for each class. A class needs 30 rated labels, a
rating is learned only from 5 examples of it, and there must be two
ratings to tell apart; a table says per class how the model did on
labels held out of training, and what could not be learned and why. Each
label is rated from its own snippet. Rejected labels are left out.

The suggestions are shown in a large grid of their own, the highest
rating first and the ones the model is least sure of first within a
rating. Pick snippets - click, Shift-click, Ctrl-click or drag a box -
and press 1 to 5 to rate them yourself; 0 takes that back. Train again
learns from your corrections too.

Nothing is in the project until it is accepted, so a suggestion never
reaches an export on its own: Accept picked (A), Accept page, or Accept
all shown at or above a certainty you choose. Accepting is one step of
Undo. A label rated in the meantime keeps its rating, and closing the
window with corrections not yet accepted asks whether to write them.

Ratings accepted from suggestions are not learned from the next time -
only ratings given by hand, or corrected in the grid - so the model is
never trained on its own guesses.

## 2.10.10 (2026-11-24)

The modes are now a menu. The Mode menu, between Labels and View, lists
Pan, Label, Cycle, View Cycle, Labeled Cycle, Object Cycle, Waterfall
and Ruler, each with its key and a tick on the one in use, and the Chain
Link switch below them. The keys work as before.

Class and Description have moved to the right end of the menu bar, with
the mode the map is in named beside them ("Mode: Cycle", and "+ Chain
Link" while that is on).

The toolbar row these were on is gone, and its height given to the map
and the panels.

## 2.10.9 (2026-11-20)

Changes to the cycle stops at objects labelled on other images (View >
Cycling, new in 2.10.8), from users' feedback.

The dashed rings drawn round each other image's label at a stop are
gone. Those labels still show as their own markers.

A stop now centres on where most of the object's labels agree, and is
zoomed to hold only those. A stray label far from the rest no longer
moves the stop or zooms it out until the object is lost, nor decides
which images the object is looked for on. Two labels far apart, with
neither in the majority, are still both shown.

## 2.10.8 (2026-11-20)

Cycles can stop at objects labelled on other images. Switch on View >
Cycling > Stop at Objects Labelled on Other Images, and Cycle, View Cycle
and Labeled Cycle stop at each object that other images' labels put on
the image shown and that is not labelled there yet - one stop per object,
zoomed to where the other images put it - before moving on to the next
image. Objects that fall in the image's no-data areas are skipped, and
an image with no such objects is framed whole as before. Dashed rings
mark where each other image's label is, and the status bar names the
object and counts the stops. Ctrl+Space steps back a stop, and back onto
an image at its last stop. Zoom in or out at a stop and the stops after
keep that zoom.

A label placed at a stop is linked to the stop's object when it is of
the object's class and the object has no label on that image yet (View >
Cycling > Link a New Label to the Stop's Object, on by default). Ctrl+Z
undoes the link and keeps the label.

With the switch on, Object Cycle also visits the images that cover the
object but have no label of it yet (the title counts them), zooms each
image to the object, and can start from a label not linked to any other.

## 2.10.7 (2026-11-20)

Cycle steps frame the image's data. An image with empty no-data columns
at its start and end, or empty rows, was framed by its whole raster: the
data sat off to one side and the empty margin filled the rest of the
view. Every cycle mode (C, V, Shift+C, Shift+O) and Zoom to Layer now fit
the view to the rectangle the data covers, one pixel wider all round.
No-data holes inside an image do not change the frame, and a stray pixel
in an empty margin does not stretch it. An image framed before its
picture has been read is framed again by its data when the picture
comes, unless you have panned or zoomed in the meantime.

Find Candidates is quicker and finds the same candidates: on survey
tiles, Contrast runs 1.6 to 2.3 times as fast and Blob 1.6 to 1.8 times.
The work on each image is spread over several processor cores, within a
memory limit, and two images are searched at a time.

## 2.10.6 (2026-11-20)

Help > Release Notes: what changed in each release of GeoLabeller, newest
first. The window opens on the version you are running, marked "this
version", with every release listed beside the notes to jump to. The
notes ship with the application, so they work installed and offline.

The installer is built with Python 3.14.10, the bug-fix release the
software is developed and tested on; earlier installers took whichever
3.14 release the build machine had.

## 2.10.5 (2026-11-19)

Object Cycle (Shift+O): step through the images one linked object is
on - the same object seen in different passes - to compare its views.
Like Labeled Cycle, but for one object's images only.

Start it from a linked label: point at it on the map and press Shift+O,
or right-click it > Cycle This Object's Images; in the Snippet Editor,
right-click a linked snippet (list or linked row) > Cycle Object on Map,
which also brings the map to the front. The toolbar's Object Cycle button
takes the Snippet Editor's snippet in hand when no label is pointed at,
and resumes the last run after a detour.

The run always opens on the image of the label it was started from, fits
each image as Labeled Cycle does, puts the object's own label forward on
each, and steps through the object's other images in the order of the
editor's linked row - one stop per image. Space and Ctrl+Space as in
every cycle; the status bar names the object and counts its images. A
view linked, unlinked or removed during the run joins or leaves it at
the next step; an image not loaded yet is loaded for it, and one whose
file is missing is skipped and counted.

## 2.10.4 (2026-11-19)

Snippet Editor: Show on Map. From the snippet you are working on to
where it sits on the map in one key: J in the Single view, the new Show
on Map button above the views, or Show on Map on a right-click of a
snippet in the list or the linked row. The main window does what Zoom to
Label does - loads and ticks the label's image, puts its marker forward,
centres the view on it - and comes to the front; the editor stays open
on the same snippet. In the linked row any view of the object can be
shown this way, whichever image it is on. A review mark the Grid has
shown is stored first, as before any other step away.

## 2.10.3 (2026-11-16)

What the 2.10.2 fixes broke, put right. After 2.10.2 six reviewers
compared it with 2.10.1; they found 3 medium and 6 low problems that the
2.10.2 fixes themselves introduced, and nothing else. All nine are fixed
here, and every 2.10.2 fix stays in place.

Imagery: a 4-band image written with default settings (red, green, blue,
near-infrared) no longer loses its dark water. GDAL marks such a fourth
band as transparency; GeoLabeller now treats a band marked that way as
transparency only when it looks like one (at least 98% fully clear or
fully opaque values), so drone orthomosaics' collars stay transparent
and an RGB+NIR file draws, exports and probes as it did in 2.10.1.
Loading a whole multi-band image with a nodata value, or a float image,
takes no more memory than in 2.10.1. Chips saved by Model Review > Learn
before this release are read again from the imagery, so training sees
what the grid shows (old saved chips stay on disk until Clear).

Model Review: opening a review file no longer changes it, except to add
where each label is to notes written before 2.10.1. A label with the
same number on another object nearby - after a project was closed
without saving, after Combine Projects, or in another labeller's project
opening a shared file - is no longer recorded as the chip's, and a later
Ignore no longer removes it; with a user name set, a label made after
the review is never taken for the chip's. Notes another tool adds inside
a chip's review entry are kept whenever GeoLabeller writes the file.
(A label moved on the map after its import is still the chip's within
the chip size; lower the chip size below that distance and the chip
shows again to look at.)

Local coordinate systems: a picture with a site-grid coordinate system
can be measured and oriented in the Snippet Editor without an error -
Size gives no metres, an orientation keeps its pixel angle with no
compass heading, and headings are not copied onto it from linked
labels. Re-seat and Import Ground Truth no longer write such a
coordinate system into the picture's record.

The 180 degree meridian: on an image that crosses it, Add waypoint here
stores a longitude between -180 and 180 (so GPX export and Import
Waypoints keep it), and waypoints, Go to Coordinates and Go to Waypoint
land on the picture while it is shown. A label's latitude/longitude on
a degrees image written past 180 (Move label, labels without
coordinates, KML, Model Review import) is now between -180 and 180, as
a click gives it.

Layers tree: dragging or removing a group under Non-Georeferenced no
longer moves or deletes a missing georeferenced image of the same
folder.

Reports: docs_workspace/retest-2026-11-16/<area>/FINDINGS.md.

## 2.10.2 (2026-11-16)

Every high and medium of the 16 November bug hunt, fixed: 4 high, 22
medium.

Labels land on the right image. Where images overlap, a click goes to
the image whose pixels are drawn at that spot: through one image's
nodata border the label goes on the image showing beneath, not on the
empty border, and a click on a tile's own pixels next to its
neighbour is no longer refused. The status-bar readout, the pixel-value
readout, the map's right-click image actions and Move follow the same
rule. A pending Move, Link with, Link by Box or chain link ends when
you start or open another project and when the label it waits on is
removed; switching mode ends a pending Link by Box as it ended Move (a
pending Link with stays through Pan, so the map can be dragged to the
other label). The ruler line no longer stays after New or Open.

Undo after removing an image: every step keeps what it did to the
images that remain, so Ctrl+Z and Ctrl+Y no longer bring back deleted
labels or give a label a class the project has lost. A Move onto the
removed image, undone, puts the label back where it came from; a Move
off it, undone, takes the label away (Ctrl+Y returns it). Locating
missing images now clears the undo history, as opening a project does.

Find Candidates adds to its file instead of replacing it: every answer
given (Ignore or a label) stays whether or not a run finds that spot
again, and so does everything on images a run did not look at; only
unanswered candidates on the images it looked at are replaced. A run on
another group, or at a stricter setting, no longer forgets what was
ignored. The status line counts what was kept.

Snippet Editor, One per object: the Grid's Accept the rest of the page
accepts only views that have no mark - a view rejected or flagged on
its own stays as it is, and out of the HDF5 export. (A, X, F, a rating
or a click on a row still sets every view of the object, whatever a
view had.) An object keeps its place in the list whichever of its views
is shown for it, so Space goes on to the next object. A review or a
rating on a linked object's row no longer rebuilds the Labeled Images
panel - about five times faster at 20,000 labels, and the panel keeps
its selection, scroll and folds.

What counts as no data: a pixel is no data only where every band holds
the nodata value, or where its alpha band or mask band says so; +inf
and -inf count as no data, like NaN. Dark water whose red is 0 is no
longer a hole on the map or replaced in float exports; drone
orthomosaics' collars are transparent, are not exported as hard
negatives and are not searched by Find Candidates; dB imagery with
-inf is no longer drawn black. Create Optimized GeoTIFFs keeps a mask
band. In the HDF5 file, nodata_filled counts pixels by this rule.

Imagery: a GeoTIFF with a local coordinate system (a site grid) is
added as a plain picture, and the add says so - it was left out without
a word. Imagery crossing the 180 degree meridian (UTM zones 60 and 1,
Fiji, the Aleutians) displays in one piece just past the map's east
edge, and clicks, markers, detail tiles, Zoom to Label, the readouts
and Save + Open work on it. (A tile wholly east of 180 still sits at
the map's west edge.) Re-seat now also notices a file of the same size
cut from another place - a pixel or more off - and its question says
which way Yes moves the labels. A same-size file whose georeferencing
alone was corrected is asked about too: answer No there; it is asked
again at the next Open.

Model Review: a label moved on the map after its import is still its
chip's at the next open when it lies within the chip size or within
the Import Options distance of where it was noted - no second label.
Opening a review file written before 2.10.1 writes each reviewed
chip's label position into it once (a read-only file is left alone).

Projects and files: Save asks before replacing a project file another
window or person saved since you opened it (Replace it / Save As /
Cancel); never for an unchanged file, Save As elsewhere, or the app's
own writes. Projects and ground truth written by scripts are checked
when opened or imported: numbers written as text are read as numbers,
a missing latitude/longitude counts as not recorded, and a value of the
wrong kind refuses the file with one sentence naming the image, the
label and the field (Import Ground Truth then changes nothing).
GeoLabeller starts when its crash-recovery folder cannot be made, and
says once that crash recovery is not active.

Panels and exports: after Add Directory on Windows the Labeled Images
checkboxes work at once; the snippet sidebar follows labels placed or
removed on the map with the Snippet Editor closed; removing a group no
longer deletes the plain pictures imported with it, and the question
counts exactly what goes; a dragged group keeps its Display Settings.
Exporting one object no longer changes the next whole-project HDF5
dialog's scopes. Sub-images are the metres asked for to the nearest
whole pixel (10 m of 1 m imagery is 10 x 10 px, was 8 x 8), and may be
odd-sized. Importing a 40,000-point GPX takes a fraction of a second
instead of over a minute.

Bug hunt report: docs_workspace/BUGHUNT-2026-11-16.md.

## 2.10.1 (2026-11-05)

The high and every medium of the 5 November audit, fixed.

Opening a project: every control is the new project's the moment it
is assigned, not when its images have loaded - the class list, the
right-click class menu, the description presets, the waypoint and
hard-negative panels, the cycle state and a hover read in flight. A
label placed while a long open ran took the previous project's class,
and the HDF5 export then dropped it.

Snippet Editor, One per object: a Needs list can now be worked down.
The list and its counts go by the view standing for the object, so
finishing that view takes the object off the list whatever its other
views still need (the linked row shows them). A review mark (A, X, F),
a confidence rating, a click on a Grid cell and the Grid's "Accept the
rest of the page" reach every view of the object as one undo step; the
Grid's Change class changes every view of each picked object instead
of leaving them in two classes. Masks, orientation and size stay with
the view they were drawn on; "Classes differ" stays an object property.

Model Review: a reviewed chip stays reviewed whatever the chip size is
set to later. The review file's note now records where the chip's
label is (label_x, label_y), and a chip finds its own label by that,
within the Import Options "same object" distance, rather than within
the chip size in hand; notes from earlier versions keep the old rule.
A label moved by hand on the map is followed as before. Next and Page
Down, a change of Show or of any filter, suggestions coming in and
opening a file all show the new page from its top and read its first
rows first; a refresh after a map change keeps your place.

HDF5 export: a file whose datasets another tool rewrote fixed-size
(the training side's optimize_h5.py does this) is refused up front,
with the reason and nothing in the file touched, instead of failing at
the first flush after promising an append; the per-object export dialog
keeps its "this object / no negatives" scopes rather than the last
export's.

At scale (13,000 images / 20,000 labels): the snippet sidebar never
asks for the whole strip when a row probe lands in the gap between
tiles (0.8 s on the UI thread and 1.4 GB of icons, once in twenty
scroll stops); undo of a class change that typed a new name redraws
the step's labels, not all 20,000 markers (3.3 s); removing a group no
longer scans the project for each of its rows (3.4 s per 500) and
removing images without labels leaves the labelled panel alone (1 s);
the recovery autosave writes only when something changed (0.2-0.5 s on
the UI thread every minute, plus a second of the writer); history
snapshots find a label by index instead of walking its image (sweep
steps were quadratic per image); markers are built without a text
item and a font each (the 1.8 s full rebuild).

Audit report: docs_workspace/AUDIT-2026-11-05.md.

## 2.10.0 (2026-11-02)

Checking linked objects in the Snippet Editor, and a wider HDF5 export.

Snippet Editor, One per object: a button after the Show box lists one
snippet for each linked object instead of every view - the view on the
image the map is showing, else the one you last clicked in the linked
row, else the lowest label id - with "(2 views)" on the caption and the
Show counts in objects. Step down the list and the linked row under the
Single view shows every view of each object: a quick check that each
group holds what it should. An object is listed while any of its views
passes Show; choosing one object in Class still lists all of its views;
the Grid view follows the same list. Remembered.

Snippet Editor, classes that differ: when the views of a linked object
disagree on a class (a labeller who was not sure used a class of their
own, say), the caption says "classes differ" and the Show box ends with
"Classes differ (N)" to find every such object. The linked row offers
"All as <class> (n)" for each class the views carry - one click gives
every view of the object that class, one undo step - and "Unlink this
view" for a view that was linked to the wrong object, which then stands
alone with its class, as the map's Unlink does.

HDF5 export: float16 joins uint8 and float32 as a pixel format (half
precision, one grayscale channel, nothing scaled on export). Appending
to a file that another tool has added a per-sample column to (a
curation mask, say) now keeps that column in step instead of leaving it
short.

## 2.9.17 (2026-10-30)

What a review of 2.9.16's four features found, fixed.

Hover snippet: no black bands when a source pixel is longer one way on
the map than the other (lat/lon imagery at high latitude, a 0.25 x 1 m
grid); a classified (paletted) raster keeps its colours instead of
blending them; switching "Oriented as on the map" no longer waits out
a read in flight; the caption says "as on the map" only when there is
a map to turn to.

Snippet Editor, Context: the wand, Grow, Remove specks and Fill
enclosed now work on the snippet's own frame alone - the surround no
longer feeds a flood, hides the largest piece or counts a hole; after
C the framed snippet is scrolled into view; C leaves a polygon in
progress alone; changing the factor while Context is off changes
nothing; a right-click in the surround still clears a line or takes
back a polygon corner; the orientation arrow is sized by the snippet.

Snippet Editor, Grid view: a double-click to look closer keeps the
snippets you had picked; a name the project refuses (hard_negative)
leaves the grid showing the real classes; a wobbling click is a click;
Change class stores a pending reject first, so Ctrl+Z follows the
order you worked in; Ctrl+drag and Shift+drag no longer show the
orientation preview.

Snippet Editor, Review: removing the last snippet of the class in view
leaves the class in view with nothing in hand, rather than jumping to
"All classes" with another label in hand; a held Delete removes one
label, not one per key repeat.

Model Review: a grid rebuild under a resting mouse (PageDown, an
import) no longer raises the description popup by itself; a long
description no longer squeezes the Details picture; the popup is kept
on screen and shows the text as written (no HTML); a description under
a later key counts when the first key is empty; the once-only rule on
import compares whole notes, case-insensitively; the popup goes when
the window is deactivated.

## 2.9.16 (2026-10-30)

Four features asked for by the users, and the editor's Remove label.

Model Review: a chip's description from the review file ("description",
"note", "comment" or "reason" per chip) shows beside the cursor when the
mouse rests on the chip, and in the Details panel; a label the import
makes or changes carries it after the model's word. The file keeps it
through every rewrite.

Snippet Editor, Grid view: pick several snippets (click, Ctrl+click,
Shift+click for a range, Ctrl+A for the page) and give them a class -
an existing one or a new name - in one step: only the picked snippets
change, never their linked siblings.

Snippet Editor, Single view: Context shows a larger area of the image
around the snippet (2x, 3x or 4x its size), the snippet's own frame
outlined and the surround dimmed, for a look at what lies beside the
object; the tools and readouts stay on the snippet. Remembered.

Snippet Editor, Review section: Remove label (Delete) takes the label
out of the project altogether - one undo step, the editor moving to the
next snippet; a label with masks or a linked object asks first.

Hover snippet (Ctrl held over a marker): the snippet is now turned the
way the map shows the image - north up - so it matches what is under
the cursor; on a survey line flown at an angle it used to look turned
against the image, as if the object had rotated. View > Hover Snippet >
Oriented as on the map, unticked, shows the file's own framing instead.
The exports and the Snippet Editor are unchanged.

## 2.9.15 (2026-10-28)

Every medium finding of the October audit: nineteen bugs and fifteen
performance items, in one release.

Model Review: a review note goes only onto the chip's own record - a
Find Candidates file rewritten under kept verdicts no longer takes the
notes onto other spots; after an import the page begins at the first
chip you had not seen; a suggestion of a class removed since the
training is not accepted, and a renamed class renames its suggestions.

Undo: Set Location is an undo step, and undoing an older step no longer
clears location tags or header reads made since; looking at an
unlabelled, since-cropped image is no longer an edit that Ctrl+Z undoes
in place of yours. A declined crash-recovery copy is set aside under a
dated name instead of being overwritten by the next autosave, and
File > Open Recovery... opens it.

HDF5 export: a float32 sample at a swath edge no longer carries NaN or
the nodata value - the empty pixels are filled with the window's own
mean, and the count is written per sample as nodata_filled (both pixel
formats); a one-object export keeps the masks of the neighbours in the
crop; the estimate counts once per burst of changes.

Snippet Editor: with Orientation, Masks and Size all off nothing is in
hand (Move label is only ever picked up by you); every new snippet
paints into the mask the picker shows; on a Needs list the snippet in
hand stays until you move off it, and the Grid view keeps its page; the
wand and the mask readout no longer stall at large snippet sizes.

Canvas: a plain image added with File > Add Image shows at once; the
labels of a hidden non-georeferenced image are hidden with it and cannot
be clicked; a click on overlapping markers picks the one under the
cursor, not the oldest; an image added after removing others draws on
top; a fine-step wheel zooms in proportion to its movement.

Also: dragging a non-georeferenced layer between groups writes the
right group; Go to Coordinates understands "Lat: 51.5, Lon: -0.12" and
keeps the sign of -0 degrees.

Faster at scale: the labelled-snippet sidebar reads only what is on
screen (20,000 labels: 18 s to 0.2 s, 274 MB to 8 MB at 3,000), and a
Display Settings change re-reads only those; Find Candidates lists
13,000 images in milliseconds, not seconds; bulk layer toggles no
longer repaint the progress bar per layer; undo away from the save
point no longer hashes the whole project; a closed Snippet Editor is
not rebuilt on every edit; import duplicate checks, the H5 estimate,
per-image stretch sampling, opening projects on a local-grid CRS,
cycle steps and clicks with many labels, tile passes with many images,
and unreachable images (tiles not re-read) are all cheaper.

## 2.9.14 (2026-10-28)

The last of the audit's high findings: detail tiles and pyramids.

A large image reads its detail tiles at the level the zoom asks for
whether or not the file has a pyramid, and whether or not the pyramid
is known yet. Whenever the overview list was empty - before the first
read reports it, and for ever in a file with no overviews - the tiles
were read at full resolution at any zoom: a saved project's large
images read and held every full-resolution tile at fit zoom, a
gigabyte for eight 16 MP images, and a pyramid-less image never let
them go. Zoomed in, the tiles still carry full detail, as before.

## 2.9.13 (2026-10-28)

The audit's exit and memory fixes.

- An undo snapshot of an image no longer copies its cached coordinate
  transformers. Each copy rebuilt two of them, so an Import Ground
  Truth step over 13,000 images took nine seconds and held a gigabyte
  for as long as it stayed in the history; it takes about a second
  now. The step also records only the images the import matched.

- Closing GeoLabeller stops everything it owns a thread for before
  the window goes: the snippet sidebar's queued thumbnail reads, the
  header sweep that follows a project open, and an export still
  running, which is told to stop and finished before the window
  closes. Exit used to wait for thousands of sidebar reads, end
  abnormally after a sweep, or abort outright with an export running,
  leaving a half-written file. A cancelled optimized-GeoTIFF export is
  now honoured between its stages as well, commits no file and says
  it was cancelled.

- Two races in the snippet reader: a picture served from the cache
  now retires an older read of the same chip still in flight, which
  used to land afterwards over the right picture; and a read is made
  with the display settings its request was made under, so a change
  in between no longer caches one look under the other's key.

## 2.9.12 (2026-10-28)

The audit's Snippet Editor pair.

- Typing an existing class name in the class box and pressing Enter
  changed the snippet in hand and then the next one as well. Qt's combo
  box also reports a choice when the box loses focus with text matching
  an item, so the change ran twice - the second time on the snippet the
  first had stepped to. The editor now takes a typed class only on Enter
  or a pick from the list; clicking away from a typed name applies
  nothing, new name or old.

- Opening another project while the Snippet Editor is showing: the
  editor is now re-seated on the new project at once. Until the new
  project's images had finished loading it kept showing the old one,
  while every rating, class, mask, heading, size or note went to the
  new project's label with the same id.

## 2.9.11 (2026-10-28)

The first of the audit's fixes: nothing written wrong, nothing lost.

- HDF5 export: the eight offset copies of a label's crop are measured
  from the window centred on the label before the edge shift. Measured
  after it, a label within the offset of an image edge got copies
  pushed clean off the object and written as positive samples of empty
  ground; copies the edge pushes onto one spot are kept once, as ever.

- Label ids are never handed out twice. Undo used to roll the id
  counter back and Clear All Labels reset it, and a review file's note
  naming a label by id could then find a later, unrelated label under
  that id: the chip jumped onto it and an Ignore deleted it. The
  counter now only rises, and Model Review takes a note's label as the
  chip's only when it lies within a chip's width of where the chip was
  reviewed. The counters no longer count as unsaved changes.

- Verdicts not yet imported are asked about. New Project, Open, Open
  Recent and closing GeoLabeller used to take the Model Review window
  down with its verdicts whenever the project itself was clean.

- A project file is read as the UTF-8 the ICD promises: one written by
  another tool with accented class names came back mangled, or would
  not open. Saving encodes the file in one call, 0.8 s faster at
  20,000 labels.

## 2.9.10 (2026-10-28)

Model Review: a big page fills faster and stays answerable; "already
in project".

A grid of hundreds of chips lagged on each page turn. Its pictures
were asked for one by one - every request made on the UI thread, and
every chip opening its image file on its own - and while the reads
streamed in the window could barely answer. Now a page's pictures are
read grouped by image, a chunk of chips per open of the file, with the
rows in view first: what is on screen comes before what is not, and a
scroll puts the reads still queued into the new order. A click
refreshes the cells once rather than three times.

The chips the window finds labelled in the project are now called
"already in project" wherever they are named: the Show already in
project box, the Show list's entry, the caption "in project: vessel
2px", the tooltip, the status line, the Details panel and the import
summary. The frame is still blue; nothing else changes.

tests/bench_review_grid.py times a grid at chosen settings in a real
window, over the test imagery or one big image.

## 2.9.9 (2026-10-27)

Model Review: the Show list isolates chips by standing.

The Show list, after All, To look at, Labelled and Ignored, now offers
three standings with their counts: Reviewed before, the done pile -
chips with a note from an earlier import whose label still stands;
Changed, an earlier answer given a new verdict in this session;
Labelled already, chips with a label within the distance on their
image, the ones that match a record already in the project. Each
lists exactly those chips whatever the Show reviewed and Show labelled
already boxes say, so the done pile is one pick away and never comes
up empty because a box is off. Predicted and Score at least still
apply. An entry is greyed while it counts nothing, except the one
chosen, which keeps its place until the list settles. The entries per
labelled class follow, as before.

## 2.9.8 (2026-10-27)

Model Review: Shift+1-9 for the tenth class on; the classes in the
user's order.

With Shift held, 1 to 9 pick the tenth to the eighteenth class, the
same way the plain digits pick the first nine: onto the picked chips,
or the chip under the cursor, which then moves on. The Labels panel
shows the chord on those rows as an arrow and a digit. Shift+1 arrives
as the shifted symbol on most keyboards, so both spellings are read,
as the map's description presets already were.

A class dragged up or down the Labels panel moves under another key,
so the keys can be put on the classes a session needs. Ignore is not
dragged and stays on top whatever is dropped above it. The order is
the user's from then on, kept by name across sessions and across
projects that share class names; a class the project gains comes
last, a renamed one keeps its place, and the New label dropdown
follows. The project's own list is left as it is, so every class
keeps its colour on the map and in the other windows. While the
filter box shortens the list nothing is dragged.

## 2.9.7 (2026-10-27)

Model Review: a Labels panel on the left of the grid.

Picking a class from the New label dropdown - open it, scroll, find
the one - was slow with many classes. A panel on the left of the grid
now stacks Ignore and every class in the project's order, each with
its colour, its key number for the first nine, and how many chips an
import gives it. One click makes the class the active label and, as
the dropdown does, gives it to the picked chips or to the chip under
the cursor, which then moves on. A box above the list shortens it as
you type; Enter takes the first class left, Escape clears the box, and
either way the keys are the grid's again. Add class... sits at the
bottom. The View row's Labels box hides the panel; the dropdown, the
Ignore button and the number keys work as before, and the panel
follows them.

Two things settled with it, after the users asked whether the Show
boxes were working:

- Chips labelled already stay on the grid to start with, framed in
  blue and counted on the status line; untick Show labelled already
  to clear them off it. They were kept off the grid by default, which
  read as the window losing them.
- A chip with a verdict in hand is never hidden by either box. A
  second look given with Show reviewed on and then the box off left
  the Import button counting chips nobody could see.

The grid's splitter is remembered under a new setting: a layout saved
for two panes would have handed the panel the grid's old width.

## 2.9.6 (2026-10-27)

The fixes of a second review of the Model Review file memory
(2026-10-27).

  * A note stands only while its label still has the class the note
    says the chip became. Undoing an import that went onto an existing
    label put the label's class back but left the chip hidden as
    reviewed under the old answer; a label given another class by hand
    on the map did the same. Such a chip is plain again, and labelled
    already by that very label, which is the true state.
  * A chip whose note no longer stands had its own label left out of
    the comparison, so it came back plain with no blue frame though a
    label sat right on it. Only a standing note's label is left out.
  * A reviewed chip is seated where its label is moved to on the map;
    its picture is now read again around the new place on the light
    refresh, not left as it was until a rebuild.
  * Find Candidates: the carry-over of the last file's notes onto
    candidates found again reaches as far as the skip does - half a
    big candidate's size when that is more than the tolerance - so a
    note is neither kept with the skip on and lost with it off.
  * A note goes only onto a record of the chip's image: a review file
    replaced since it was opened no longer gets notes stamped onto
    whatever records sit at the old places, and the window says so
    once.
  * The file is rewritten with the team's own characters, not escaped.
  * The ICD says what Combine Projects means for a review file's
    notes: the second project's labels are renumbered, and its notes
    stand only where the old id still lands on a label of the right
    class on the right image.

## 2.9.5 (2026-10-27)

The fixes of a bug review of 2.9.4 (2026-10-27).

  * Model Review: closed by the user, the window is only hidden, and
    a label placed, removed or undone meanwhile was not compared with
    its chips - it showed stale when opened again, and was refreshed
    on every undo while nobody looked. It now catches up when shown.
  * A sweep of the labels - Clear All Labels, an import of ground
    truth, images removed with their labels - reaches the window too.
  * Labels > Rename Class... renames what the review file's notes say
    a chip became, in the window and in the file; a reviewed chip no
    longer reads the old name.

## 2.9.4 (2026-10-27)

Model Review against the project's labels, and the review file as the
memory (asked for 2026-10-27).

  * Labelled already: a chip whose centre lies within a distance of a
    label the project already has on its image - placed by hand, by an
    import, by an earlier round - is told apart: framed in blue with the
    label's class and how far off it lies, unlike any class's colour and
    unlike the shade of a chip reviewed before; kept off the grid unless
    Show labelled already; counted on the status line. Details marks
    those labels on the picture and lists them. Ignore leaves the label
    alone; a class goes onto the nearest of them, which takes the class;
    Add as a new label here, ticked in Details, makes a label beside it
    instead, spared the duplicate rule. Labelled within sets the
    distance, the Import Options distance to start with; the comparison
    is made again whenever the labels change - place a label on the map
    and the chip beside it turns blue - so it never goes stale.
  * The file is the memory: an import writes a note on each chip's
    record in the review file - what it became, the label it made or
    took over, who and when - atomically, everything else as it was;
    nothing goes into the project but the labels, and the project file
    no longer carries a review memory (format 5.3; a project's old
    memory is moved into a review file the first time that file is
    opened). A reviewed chip whose label is gone - removed by hand, an
    import undone - is plain again; an ignored one stays ignored. A
    file that cannot be written is said once; the notes hold for the
    session.
  * Find Candidates: the last run's candidates file is the memory now.
    A candidate it holds an answer for is kept aside in the new file
    with that answer, not dropped, so the answer survives run after
    run; with the skip off the answers ride onto the candidates found
    again.
  * The detectors: the blur of the background and spread maps across
    tile edges, 1.1 s of every image, is a weighted sum of the few tiles
    a window overlaps, exactly; the Blob ladder's weight blur separates
    for an image without nodata holes. Contrast 5.6 -> 4.4 s an image,
    Blob 10.8 -> 9.7 s (6.1 s without holes).
  * The How-To's Model Review chapter and the ICD's sections on the
    file and the project format are rewritten accordingly.

## 2.9.3 (2026-10-26)

The fixes of a bug review of the 2.9.2 tools (2026-10-27).

  * Hover Snippet: with Ctrl held, every pixel of mouse motion searched
    every marker on the canvas - a scan of twenty thousand markers per
    move. The markers are searched once, when the mouse comes to rest;
    while a snippet is up, only its own marker is asked whether the
    mouse is still on it. Choosing another size takes down the snippet
    up at the old one; closing the window takes it down too, and stops
    a read still going for it.
  * Rename Class: undoing a rename took back the Model Review window's
    verdicts in hand that named the new class, as a class the project
    had lost. They follow the name back, before the class list changes
    under them (and forward again on redo).
  * Clean Close Labels: the window showed pairs of labels removed on
    the map, by Clear All Labels or by a removal elsewhere, until the
    next Find; it now follows every change of the labels, and a pair
    the window shows always exists. A pair unlinked since the search
    no longer reads "one linked object". Enter, pressed while deciding,
    restarted the search from the first pair: there is no default
    button now.
  * Label Markers: in Waterfall mode the views an object's one marker
    stood for were still projected, in full, onto every stacked image
    - the pile the mode was meant to clear. The projections follow the
    marker drawn, and are redrawn when the roles change. A dot was a
    small target: it is as easy to hit as a full marker.

## 2.9.2 (2026-10-26)

Keeping labels tidy: four tools asked for on 2026-10-26.

  * Labels > Rename Class...: a class's new name on every label that
    has it, on every image, and in Model Review's memory of what a chip
    became; the class keeps its place in the list and so its colour;
    one undo step. A name the project already has merges the two
    classes, after a question with the counts. Edit Classes read a
    changed line as a class removed and another added, which deleted
    the labels; it now says so and points here.
  * Labels > Clean Close Labels...: every two labels on one image whose
    centres lie within a distance in pixels (the Import Options
    distance to start with; any classes, one class only or different
    classes; with or without pairs that are one linked object), shown
    one pair at a time with both snippets, a cross on the label and a
    ring on its partner, class, position, description and labeller
    under each, Show on map for either. Keep both, Remove left, Remove
    right, Remove both (K, 1, 2, 3; the arrows move); each removal is
    one undo step, made at once, and every pair that involved a removed
    label goes with it. The survivor keeps its links. A grid of the
    distance makes the search one pass over the project.
  * View > Label Markers: how the labels of one linked object are
    drawn. Every label, as before; one marker per object, its caption
    counting the views, with the other views as small dots still there
    to click; or one marker per object alone. The marker drawn is the
    label on the image being shown, else the one a panel asked to see,
    else the one on the topmost layer switched on. Remembered.
  * View > Hover Snippet (hold Ctrl): with Ctrl held, rest the mouse on
    a label's marker and a small window beside the cursor shows its
    snippet - at 96 to 256 pixels, or Off - drawn the way the group's
    Display Settings draw it, with its class, image and description. It
    goes when the mouse leaves the marker, Ctrl is released, or
    anything is clicked.
  * The How-To gains chapter 28 and the F1 help a Keeping Labels Tidy
    section; the ICD describes each tool. The project file format is
    unchanged.

## 2.9.1 (2026-10-26)

Find Candidates: the fixes of a bug review (2026-10-26).

  * Looking for both bright and dark, a highlight's patch ran on into
    the shadow touching it and the candidate sat in the shadow, and a
    flat shadow's own peak lost to a brighter neighbour's and was never
    a candidate at all. Both detectors now pick their peaks per sign,
    with a patch of the peak's own sign: a highlight beside its shadow
    is two candidates.
  * Merging overlapping candidates kept the strongest but moved it to
    the middle of both patches together, which put a small strong
    object far from itself; the strongest stays where it is.
  * Closing the main window, or replacing the project, with a run still
    going left its thread to be destroyed with the window and aborted
    the process; the detector's window now closes with the main one,
    stopping or detaching its run. A close during a run no longer
    writes a partial file over the last one and opens it.
  * An image whose size was never recorded took its metres as pixels;
    the raster's header now gives its width.
  * Memory bounded: the blurs come one at a time instead of a whole
    bank held at once, the difference-of-Gaussians normalisation is
    measured at a small sigma, the largest size is cut to a quarter of
    the image, the block blur never pads past it, and a run that still
    runs out of memory is reported as unreadable, not as a failure.
  * Check against my labels counts a label as found when it lies
    within the candidate's own patch (or the smallest size), not within
    half the largest size, which found labels 25 metres off.
  * The skip of what was reviewed takes Model Review's own Same chip
    within distance (or half the candidate's size, if more) and leaves
    a same-named image in another group alone, as the tooltip said.
  * The blob detector makes its two patch masks once, not per peak; a
    tile of nothing but nodata no longer warns on the console; the
    check's status line and table read cleanly when images could not
    be read.

## 2.9.0 (2026-10-26)

Find Candidates: a detector proposes, you check.

  * Labels > Find Candidates... runs a detector over the selected
    group, the images shown, or every image, and proposes the places
    that stand out from their surroundings - bright or dark, of the
    sizes you give in metres (pixels where an image has no ground) -
    on any imagery: the detectors read the raw values of the bands the
    display draws, so sonar, radar and optical are alike to them.
  * Two detectors, each documenting its own controls in the window,
    the tooltips and the How-To, every number with what raising it
    does: local contrast against a robust background (the CFAR family,
    any shape) and a difference of Gaussians over the size range
    (compact patches of about the sizes asked for). Numpy only.
  * Check against my labels runs a sweep of sensitivities over the
    labelled images and says what share of your labels each would
    have found and how many candidates an image gets; double-click a
    row to take that sensitivity. A budget of candidates per image,
    and skips near labels and the memory, are yours to set; all of it
    remembered.
  * Run writes the candidates beside the project, as
    <project>.candidates.json, and opens them in Model Review, which
    takes the class "candidate" and shows "found" and the score where
    a model's chip shows its class: verdicts, Ignore, re-centring,
    Import and the memory work unchanged, so a second run shows only
    what is new. ICD section 3.13 specifies the file; the How-To's
    chapter 27 and the F1 help cover the window.

## 2.8.17 (2026-10-25)

Model Review: the fixes of a second bug review (2026-10-25).

  * The memory's entries carry the image's group (project format 5.2,
    an optional field): two same-named images in two groups keep
    entries of their own, where before the second chip lost its entry
    after an import and was imported again and again. When two chips
    still want one entry, the better match keeps it and the other
    takes its next.
  * A reviewed chip whose label is gone - deleted, or dropped as a
    duplicate of one already there under the default policy - settles
    where it stands: nothing to move, nothing pending. Before, it stayed
    on the Import button for ever, imported and dropped again each time.
  * A move alone only moves an existing label: with the label gone it
    makes neither the label nor its class again, and the import counts
    it as "with no label left to move".
  * A staged centre move on a reviewed chip survives an undo or redo in
    the project and a tolerance change; the chip is re-seated only when
    its label is somewhere else than its anchor.
  * The importer changes no label on another image of the same name.
  * The status line and the import question count re-centred chips
    apart from class changes.

## 2.8.16 (2026-10-25)

Model Review: an import that would not stay imported.

  * One object the model flagged under two classes is two chips at one
    centre. After their import each had a memory entry of its own, but
    the window matched both chips to the first entry by distance alone;
    under 2.8.14's one-entry-per-chip rule the other chip lost it, came
    back as new, stayed on the Import button, and was imported again
    and again, a memory entry each time. The memory now answers with
    the entry the model called the same class first, then the nearest.
  * A chip on each of two same-named images in two groups was folded
    as one chip given twice; the file's duplicates are now told apart
    by the image as the record writes it.

## 2.8.15 (2026-10-22)

Export > Labels (KML)...

  * Every label with a position on the ground goes to a KML 2.2 file,
    which Google Earth, QGIS, ArcGIS and GDAL read: a Folder per class,
    a Style per class in the canvas's marker colour, a Placemark per
    label named by its class, with the label's own description, its
    creation time, and its particulars - image, group, pixel position,
    ids, size, heading, rating, review, who made it - as ExtendedData.
  * Left out and counted on the status line: a label on imagery
    without georeferencing that has no recorded position, and a label
    rejected in review (as the HDF5 and sub-image exports leave it).
  * ICD section 3.12 specifies the file; the How-To's Export page and
    the F1 help list the entry.

## 2.8.14 (2026-10-22)

Model Review: the fixes of a bug review (2026-10-22).

  * Combine Projects shifts the second project's label ids, and now
    its review memory's label ids with them: a chip re-reviewed later
    edited whichever of the first project's labels carried the number.
  * One meaning of "moved" for the window and the import: a move is
    measured from where the project has the chip - its label's place
    once imported, else the file's centre. A right-click (Clear) puts
    the centre back there; before, it went to the file's centre, the
    chip read as moved, the import offered it and moved nothing.
  * A chip reviewed in an earlier round comes up where its label is,
    including after an undo in the project; a memory entry answers
    for one chip only (the nearer of two within the tolerance).
  * A new verdict on a reviewed chip outlives the tolerance box and an
    undo in the project; only a verdict that is what the chip already
    became is dropped.
  * Training leaves out the label an outdated verdict made, so a
    re-reviewed chip no longer teaches both its old and its new class.
  * The import indexes the memory once instead of scanning it per chip.
  * Closed with pictures still coming and shown again, the window asks
    for them; a close that leaves training running frees the Learn row
    and the next Train starts a learner of its own.
  * A Ctrl-click or right-click no longer leaves a plain click behind
    for a double-click to take back.
  * The Details panel says when a chip's picture could not be read;
    Show on image reads the image again after its display changed; the
    Reference strip asks for each picture once on a rebuild.

## 2.8.13 (2026-10-21)

The Details panel's buttons fit again.

  * Measure (R) and Show on image (M) sit on a row of their own under
    the Context box: beside it they no longer fit the panel at its
    default width, and Show on image was cut off (2.8.12).

## 2.8.12 (2026-10-21)

A measure tool in Model Review.

  * Measure (R) in the Details panel turns the chip's picture into a
    ruler for when the size of a thing decides its class: click two
    points and the length between them is shown in metres, from the
    image's georeferencing, and in pixels. An image without
    georeferencing - or one whose georeferencing cannot be used - gives
    pixels alone, and nothing stops. A right-click clears the line,
    another click starts a new one, and the tool stays on until R is
    pressed again. F1 lists the key.

## 2.8.11 (2026-10-21)

A reviewed chip can be re-centred; duplicate labels on import.

  * Model Review: a chip reviewed in an earlier round - shown with
    Show reviewed - can now be re-centred by a double-click, as any
    other chip, and the next Import moves the label it made; a picked
    reviewed chip takes Auto-center too. The grid says "(moved)", the
    status counts it with the chips changed from an earlier round, and
    undo takes it back.

  * File > Import Options... says what becomes of an imported label
    that lands on a label already here - the same image, within a
    chosen number of pixels - for Import Ground Truth, Combine Projects
    and a Model Review import alike: keep the labels already here and
    drop the new (the default), keep the new and drop the old, keep
    both, or ask, a window listing each pair for a decision by hand.
    What is dropped goes with the import's undo step.

## 2.8.10 (2026-10-20)

The re-seat says what "unchanged" meant.

  * After a re-seat the status line counted every image it did not
    alter as "unchanged", which lumped together an image that matches
    the record, one that could not be re-seated - unreadable, without
    georeferencing, in another CRS - and a regrid declined. The three
    are now counted apart, and the background sweep after an accepted
    offer, which reports only on the status line, opens the report
    whenever an image could not be re-seated, so the reason is in
    front of the user.

## 2.8.9 (2026-10-20)

A shared project saved on the cropped imagery matches it throughout.

  * Accepting the re-seat offered after a project opens now also
    brings every other image's record - size, georeferencing, and so
    the corners and geodesic extents written with it - up to date with
    its file, in the background, with nothing to move and nothing
    asked. A project saved afterwards describes the cropped files
    throughout, labelled images or not, and opens quietly from then on.

  * The Model Review memory's chip centres move with their image, by
    the crop offset or through the old and new georeferencing, so the
    next round's file still finds the chips reviewed before.

## 2.8.8 (2026-10-20)

Images without labels keep up with their files too.

  * An image without labels whose file was cropped since the project
    recorded it had a stale record, which would have misled a later
    re-seat once the image gained labels. Now its record is brought up
    to date - nothing to move, so nothing asked - the moment its layer
    first reads the file, and File > Re-seat Labels to Imagery...
    covers every image, counting such records in its report. The look
    at the files after a project opens still reads only the labelled
    images' headers, so a metadata-only load stays free of file reads;
    its offer says how many of the differing images carry labels.

## 2.8.7 (2026-10-20)

The re-seat report fits the screen.

  * After re-seating labels (2.8.6) the report came as a message box,
    one line per image - and a message box grows with its text, so a
    project of many images pushed its OK button off the screen, with
    nothing to click. The report is now a window of its own that
    scrolls and fits the screen, with the outcome in a line at its top;
    images that match the record are folded into one line.

## 2.8.6 (2026-10-20)

Labels re-seated on imagery cropped since it was labelled.

  * A project shared with someone whose copies of the same GeoTIFFs
    were cropped - columns or rows cut off, the georeferencing updated
    to match - now opens right: GeoLabeller notices a labelled image
    whose file differs in size from the project's record and offers to
    re-seat its labels. By the georeferencing a crop is a whole-pixel
    shift, and every label and mask moves by it; labels in the cut-off
    part are dropped and the project is told the new size. If the pixel
    size changed as well, the labels are placed by their latitude and
    longitude after a question, and masks, painted on the old pixels,
    are dropped. One undo step, with a report per image.

  * File > Re-seat Labels to Imagery... does it any time. A project
    from before affines were recorded goes by its labels' own latitude
    and longitude. Locate Missing Images accepts a cropped copy of an
    image as that image.

## 2.8.5 (2026-10-15)

Model Review: a second look at a chip reviewed before.

  * A chip reviewed in an earlier round - shaded, with Show reviewed on
    - now takes a new verdict like any other: a click, a number key, 0
    for Ignore. The next Import changes the label it made, removes it
    for Ignore, or makes one for a chip ignored before, and the memory
    follows; one undo step, as ever. The grid says "(changed)", the
    status counts them, and the Show list files the chip under its new
    class. Giving a chip the class it already became is no change.

## 2.8.4 (2026-10-15)

Model Review: the grid clipped by the model's score.

  * A "Score at least" box on the View row keeps only the chips the
    model scored at least that - 0.95 to tackle its surest mistakes
    first, 0 for all of them. The status bar counts what the floor
    hides, and the value is remembered.

## 2.8.3 (2026-10-15)

The Reference strip's columns are yours to choose.

  * A "per row" box at the head of Model Review's Reference strip sets
    how many chips share a row, 1 to 6: one makes the single column of
    big chips of 2.8.2, two is the default. The chips already there
    move and resize; the choice is remembered.

## 2.8.2 (2026-10-15)

Model Review's controls tidied, and the Reference strip one column.

  * The window's controls sit in three rows now - File, Labelling and
    View - instead of five and a paragraph. The chip size, columns and
    chips per page join Show reviewed and Same chip within in a Settings
    row that folds out from the end of the View row; the training and
    suggestion controls fold out behind a Learn button beside it, both
    remembered. The hint under them is one grey line.

  * The Reference strip under the Details panel shows its chips one
    above the other, each the width of the strip, instead of three to a
    row.

## 2.8.1 (2026-10-15)

The values under the cursor, and a legend for a colour table.

  * The status bar now shows, beside the cursor's coordinates, the
    pixel under it: every band's value, nodata named as such, and for
    imagery drawn through its colour table the value's colour. The
    pixel is read off the UI thread, newest position wins. View >
    Pixel Values in Status Bar turns it off; the choice is remembered.

  * View > Display Settings... for a group whose imagery carries a
    colour table gains a Legend row: the values the imagery holds, each
    with its colour and its share of the pixels, greyed while the table
    is unticked.

No change to the project format (5.1).

## 2.8.0 (2026-10-14)

Imagery with an embedded colour table is drawn in its own colours.

  * A GeoTIFF band that carries a colour table - a palette, as a
    classified raster or an indexed-colour image has, and as QGIS draws
    it - is now drawn in those colours by default, on the canvas, in the
    detail tiles, and in every snippet view. The stretch does not apply
    to such a band; brightness, contrast and gamma still do. Exports
    read the values, as before.

  * View > Display Settings... offers a "Draw through the file's own
    colour table" tick for a group whose imagery carries one; untick it
    to draw the values as grey instead. The choice is saved with the
    project: format 5.1, which adds only this flag.

## 2.7.3 (2026-10-14)

Model Review: the Suggested, Show, Predicted, New label and Reference
boxes now grow to their longest entry whenever their entries change.
They sized themselves once, when the window was first shown with nothing
in them, and cut the class names that arrived later.

The installed build is unchanged in behaviour. No project format change
- still 5.0.

## 2.7.2 (2026-10-13)

Model Review: the labelled chips of one class, and a dozen fixes.

  * Show: can now list the chips labelled one class - "Labelled rock
    (12)" - as well as all the labelled, the ones still to look at, or
    the ignored. Predicted stays what the model called them.

  * Fixes from a look over the code: a training still running when the
    project was closed could take the whole application down; a
    training finished after another file was opened put its suggestions
    on the wrong chips; a class whose chips could not be read was
    silently learned around, with every suggestion the other class; a
    double-click on a chip whose picture had not arrived left it
    labelled; a chip-size change could show chips at the old size;
    Show on image kept a chip of a file since closed; the reference
    strip gathered the project's labels on every cursor step; and a
    handful of smaller ones.

The installed build is unchanged in behaviour. No project format change
- still 5.0.

## 2.7.1 (2026-10-13)

Model Review opens at once, and shows what a class already looks like.

  * In a source run with PyTorch, the import that made the window slow
    to open now begins on a thread of its own as soon as the main
    window is up. Open Model Review before it is through and the Learn
    row says "Looking for PyTorch..." until it is; everything else in
    the window works meanwhile.

  * A Reference strip under the Details panel shows chips already given
    a class - pick the class at its head - with this session's verdicts
    first, the newest at the top, then earlier rounds and the project's
    labels of that class, to hold up against the chip in hand. The
    divider between the two panels is yours, and remembered.

The installed build is unchanged in behaviour. No project format change
- still 5.0.

## 2.7.0 (2026-10-13)

Model Review's learning, faster and in view.

  * Chips are read from the imagery once - each file opened once, its
    windows in row order - scaled small and saved under the system's
    temporary folder, so training and suggesting read them and not the
    GeoTIFFs. Save snippets in the Learn row does that ahead of time
    for a whole file; Clear deletes them. Suggesting for thousands of
    chips no longer opens the file for every one.

  * Backbone chooses the model: the small net learned from scratch, or
    ImageNet's ResNet18 with a linear head on its features, which does
    better with few verdicts (torchvision and a 45 MB weights file,
    fetched once; requirements-ml.txt has both). Suggest runs the model
    again without training - after a chip-size change, or on the next
    round's file.

  * A training window shows the phase, a progress bar, the time gone
    and Stop; when the work is done, the report: a table per class, the
    confusion matrix, the loss and held-out accuracy by pass, and how
    many suggestions the Accept threshold would take, per class, as the
    threshold box turns. Results... brings it back. The suggestion
    controls have a row of their own.

Source runs with PyTorch, as in 2.6; the installed build is unchanged.
No project format change - still 5.0.

## 2.6.1 (2026-10-13)

A fix for 2.6.0's Train on verdicts in a source run on Windows: the
entry point loaded Qt before the app package, so Qt's own older C++
runtime was in first and PyTorch's DLLs failed to initialise (WinError
1114). python main.py now loads the app package first. The README says
to do the same - import app, or torch, before PyQt5 - in a script of
your own.

The installed build is unchanged in behaviour.

## 2.6.0 (2026-10-13)

Model Review learns from the verdicts.

  * Train on verdicts, in the new Learn row, teaches a small model what
    you have decided - the verdicts on the grid, the project's memory of
    earlier rounds, and the project's own labels of those classes,
    Ignore counting as a class - and reports its accuracy on a fifth of
    the chips held out. Every chip still to look at then shows a
    suggested class and a confidence in its caption, and the grid goes
    over to the surest suggestions first; Suggested narrows it to one
    class. S accepts the suggestion for the chip under the cursor or the
    picked chips; Accept the page at or above takes every suggestion on
    the page at least as sure as the box says, as one thing to take
    back. Training runs in the background, with Stop, and takes seconds
    to a minute.

  * This needs PyTorch, which the installer leaves out: it is for
    GeoLabeller run from source, with requirements-ml.txt installed for
    the CPU or for an NVIDIA GPU (the README has both lines). The
    installed build shows the row greyed out, with the reason.

No project format change - still 5.0.

## 2.5.6 (2026-10-13)

Model Review: looking closer.

  * A Details panel beside the grid shows the chip in hand - the one
    under the cursor, the one last clicked, or the one the mouse rests
    on - read again at several times the chip size, with a box where
    the chip is and the mark where the label will go, and under it
    everything known: image and position, latitude and longitude, the
    model's call and score, the verdict, a moved centre, an earlier
    round's verdict. Context sets how much of the image round the chip
    it shows; untick Details to give the grid the whole width.

  * Show on image (M) opens the whole image in a window of its own,
    with a box the size of the chip where the chip is and a mark at
    its centre. The wheel zooms, a drag pans, F fits, Escape closes;
    the window follows the chip in hand, and keeps the zoom when the
    next chip is on the same image.

  * Show: lists all the chips, the ones still to look at, the
    labelled, or the ignored. The list settles when a page turns, so a
    chip just labelled stays put until then; Next then brings the
    chips after the ones on screen, however many left.

No project format change - still 5.0.

## 2.5.5 (2026-10-13)

Model Review, worked from the keyboard.

  * The arrow keys move a cursor from chip to chip; a number key or
    Enter labels the chip under it and moves on, Space moves on, 0
    ignores, Delete clears. Past either end of a page the next page
    comes up. I ignores every chip on the page still without a verdict
    and turns the page - the few chips worth a class done, the rest in
    one stroke.

  * Ctrl+Z takes back the last thing done in the window - a verdict, a
    run, a page's ignores, a centre move - before it reaches the
    project's history; Ctrl+Y does it again.

  * Import to project sits at the bottom right beside the pager, bold,
    with its count, and answers Ctrl+Enter. It says what it is about to
    do - how many of each class, ignored, re-centred - and asks; the
    window's status line then says what came of it.

  * The controls sit in three rows: File, Labelling, View; the settings
    seldom changed fold away under More. The New label box shows each
    class in its colour; the caption names the model's call when the
    classes are mixed.

No project format change - still 5.0.

## 2.5.4 (2026-10-08)

Model Review: a reported bug and seven found looking for more.

  * A chip re-centred further than Same chip within stayed on the grid
    after its import, verdict and all, and the next import made its
    label a second time. The memory is asked by the centre the file
    gave, as it records it. Projects already holding such duplicates
    keep them; each import was one undo step.

  * A double-click or Auto-center on a picture still being re-read
    after a chip-size change mapped through the new size's frame, 80 px
    wrong. Every picture now carries the frame it was read with.

  * Opening another file, or closing the window, over verdicts not yet
    imported now asks; closing the main window closes this one too.

  * A class removed from the project takes its verdicts back; chips
    whose image left the project are dropped; the same chip given twice
    in a file is folded into one; the number keys work again after a
    control was used; a project without classes says to add one.

No project format change - still 5.0.

## 2.5.3 (2026-10-07)

Model Review: five things the users found.

  * The window opened wider and taller than a laptop screen, with no way
    to shrink it. It now opens no larger than nine tenths of the screen,
    the line summarising the file wraps instead of widening the window,
    and the grid needs no sideways scrollbar.

  * The centre mark is plain to see: a ring round the pixel the label
    will take, with four arms, a dark line under a light one.

  * Ctrl+Z takes a centre move back - an Auto-center run as one step, a
    double-click, a right-click's restore - before it reaches the
    project's history; Ctrl+Y does it again.

  * A moved centre shows at once: the mark jumps to the new centre on
    the old picture, and settles in the middle when the chip, read again
    around the new centre, arrives.

  * A double-click no longer labels the chip; it only puts the centre on
    the object.

No project format change - still 5.0.

## 2.5.2 (2026-10-06)

Model Review: the label's centre on the object.

  * Every chip wears a centre mark showing where the label will land.
    Double-click the object and the centre moves there; the chip is
    read again around it and the mark turns cyan. A right-click, or
    Delete on picked chips, puts the centre back where the file had it
    along with taking the verdict back. Centre mark can be switched off.

  * Auto-center makes a first guess for the picked chips, or the whole
    page: mean shift to the nearest patch that stands out from the
    chip's background, bright or dark. A chip of plain water is left
    where it is. Look at where it landed; a double-click corrects it.

  * The memory keeps the centre the file gave, so a moved label is
    never mistaken for a new chip next round.

No project format change - still 5.0.

## 2.5.1 (2026-10-06)

Model Review at scale.

  * A file of thousands of chips opens as quickly as one of ten. The
    grid is built a page at a time (Per page, 60 by default; Previous
    and Next, PageDown and PageUp), so the window no longer freezes
    for seconds on a large file or holds every chip's pixels. The
    status counts the whole list; a selection lives on its page; a new
    page size keeps your place.

  * The memory of earlier verdicts is indexed by image and position,
    so "reviewed before" is found in constant time: against 20,000
    remembered verdicts the lookup takes a tenth of a second where it
    took over half a minute at 5,000. Importing 20,000 verdicts takes
    under two seconds; reopening a file the same.

  * The predicted-class counts follow the Show reviewed switch.

No project format change - still 5.0.

## 2.5.0 (2026-10-06)

Model Review: the model team's mistakes, labelled and imported.

  * Labels > Model Review... reads a JSON file listing the chips a classifier was confidently wrong about - clutter it
    took for a real class - and shows them in a grid, one predicted
    class at a time, surest first. Images are matched by file name,
    centres stated at another image size are scaled across, and the
    chip size is yours, as in the Snippet Editor.

  * A click gives a chip the active label - a project class, one added
    from the window, or Ignore - a Shift-click a run of them; Ctrl-click
    picks several for a number key. Import to project makes one label
    per labelled chip, at its centre with lat/lon, your attribution and
    a description saying what the model said. Ignored chips make no
    label. One undo step.

  * The project remembers every verdict. The next round's chips that
    were reviewed before are kept off the grid; Show reviewed brings
    them back, shaded, with what they became. Label the clutter as a
    new class, retrain, review the next file.

Project format 5.0: an optional top-level "model_review" array. Older
projects open unchanged. The review file's format is ICD
section 3.11; the How-To has a new Part 3.

## 2.4.4 (2026-10-05)

Two requests from the labellers.

  * Snippet Editor: the Label point box decides whether the ring over
    the label is drawn, whatever tool is in hand. The Move label tool
    used to draw it regardless, so a rater flipping through snippets
    with Move in hand could not clear it. A click with the box off
    still moves the label.

  * Display Settings: which values are drawn black and white, for
    16-bit and floating-point imagery. Percentiles of each band's own
    values (2 to 98 is the default and draws exactly what was drawn
    before), or one fixed range of values for every band - 0 to 1 for
    imagery that is already a fraction, which is then simply times 255.
    The window says what the group's imagery holds, offers 0 to 1 when
    the values lie within it, and Sampled range fills in the lowest and
    highest found. 8-bit imagery is drawn as stored. Display only:
    exports read the raw bands as before.

Project format 4.9: an optional "stretch" object on a display_settings
entry. Projects from 4.8 and earlier open unchanged.

## 2.4.3 (2026-10-05)

Six Snippet Editor bugs, found by looking for them.

  * A brush stroke in progress when the snippet changed (Space pressed
    mid-drag) went on painting the next snippet, and was committed to
    it. What a stroke has painted now stays on the snippet it was
    painted on; a line begun on the last snippet is not drawn on this.

  * A double-click with the Move label tool moved the label twice - two
    undo steps for one spot. Once now.

  * With a number box focused (after a click on Brush, Tolerance or
    Snippet) every key went to the box, which does nothing with a
    letter: A, O, [ and the rest were lost until something else was
    clicked. Only the keys a number box edits with stay with it now.

  * After a class was picked from the Label class box, or a name from
    the mask-name box, the box kept the keyboard: Space typed a space
    instead of stepping. A choice made from the list hands the keys
    back; a name being typed keeps them.

  * The linked row kept showing a moved label's old crop; it reads the
    picture again where the label is now.

  * The open editor did not follow labels placed, removed, linked or
    unlinked on the map, and an edit on a label removed there was
    dropped without a word. It follows those now, once per gesture,
    while it is showing.

No project format change - still 4.8.

## 2.4.2 (2026-10-02)

A How-To guide, and Help > How-To to open it.

  * docs/GeoLabeller-HowTo.pdf: a user's guide in two parts - labelling
    imagery on the map, and the Snippet Editor - 42 pages, a picture of
    each step. Chapters are named for what the reader is doing; each
    cycle mode has an entry saying what it is for; the hard negative
    flag has a chapter of its own; the contents link to their pages.

  * Every picture is a screenshot the guide's build script takes from
    the running application, so the pictures show this version. The
    guide is rebuilt with each release, as the ICD is.

  * The guide ships beside the executable with the ICD, and opens from
    Help > How-To.

No project format change - still 4.8.

## 2.4.1 (2026-10-02)

Four more ways to make a mask in the Snippet Editor. No model: the wand
compares pixel values, and nothing new is shipped in the installer.

  * Polygon (key G). Click round the object, then double-click, press
    Enter, or click the first corner, and the shape is filled into the
    mask in hand. Begin with a right-click to cut the shape out instead.
    Right-click or Backspace takes back the last corner; Escape gives
    the shape up. A pixel is in the shape when its centre is.

  * Wand (key D). Click the object and the connected pixels that look
    like the one clicked join the mask; a right-click takes them out.
    Keep the button down and drag right or left to take in more or less
    - the patch is shown before it is taken - and let go. "Alike" is
    judged on the snippet as it is displayed, within a Tolerance (0-255,
    beside the brush size) that the drag sets and the editor remembers.

  * Grow, Shrink and Remove Specks, in the Masks section. Grow and
    Shrink make the mask one pixel wider or narrower all round; Remove
    Specks drops its separate pieces smaller than one stamp of the
    brush, and the largest piece always stays. Grow, Fill Enclosed,
    Shrink closes a mask the wand left ragged.

  * Keys for the brush. [ and ] make it smaller and larger; H, held,
    shows the imagery under the masks.

Every one of these is a mask edit like a stroke: one undo step, stopped
by the other masks unless Allow Overlap is on, and taking only from the
mask in hand. The line above the snippet now says what the tool in hand
does, and the Snippet size box has moved down to the row with the zoom
and the brightness.

No project format change - still 4.8.

## 2.4.0 (2026-10-02)

Seven improvements to the Snippet Editor's workflow.

  * Quick on large projects. At 2,000 labels the editor opens in about
    0.1 s (it took 2.5 s), and undo, redo and a class change with it
    open take about 0.1 s (they took 1.4 s). The list reads thumbnails
    for the rows in view, and the Grid view is built while it is showing.

  * The zoom you set stays. A zoom set with the wheel carries to the
    next snippet, with the view held on the label, and through undo and
    the brightness and contrast sliders. Fit, beside the zoom readout,
    goes back to the whole snippet.

  * The label's own point is marked on the snippet with a ring - near an
    image's edge it is not the middle. "Label point" unticks it.

  * Move label (key V): click where the label belongs and it moves
    there, on its own image, to a fraction of a pixel when zoomed.
    Masks, orientation, size, rating and review stay as they were. One
    undo step. The tool stays in hand, so a pass correcting positions -
    after importing ground truth at another resolution, say - is Space
    and a click per snippet.

  * Keys for the tools: O Orient, P Paint masks, M Measure, V Move
    label. The key is on each button.

  * "Advance after rating" in the Confidence section: ticked, a key 1-5
    rates the snippet and moves to the next. Off until ticked, and
    remembered; 0 and the buttons stay on the snippet.

  * Notes: a section at the top of the column shows the description and
    the Group ID of the snippet in hand and edits them in place. Stored
    on leaving the field, or with Ctrl+Enter (Enter for the Group ID);
    Escape puts back what was stored. The Group ID goes to every linked
    label; the description is the label's alone.

  * Review grid: the Grid view has a Review mode beside Orient. Each
    snippet wears what its review found; a click rejects it (again takes
    that back), a right-click flags it to look at again, and a
    double-click still opens it in the Single view. "Accept the rest of
    the page" - or A - accepts everything left unmarked and goes to the
    next page, as one undo step.

No project format change - still 4.8.

## 2.3.11 (2026-10-02)

Import Ground Truth onto the same imagery at another resolution.

Ground truth made on downsampled copies of the imagery can now be
applied to the full-resolution originals:

  1. start a new project;
  2. File > Add Directory on the full-resolution folder - a mirror of
     the one the ground truth was made on, with the same filenames;
  3. File > Import Ground Truth, and pick the project or GT file made
     on the low-resolution copies.

  * An image of the same name and another size is taken to be the same
    imagery at another resolution (it used to be refused as a
    "Different file"). The preview marks it "Found, other resolution"
    and shows both sizes. Nothing checks that the two cover the same
    ground: loading the right folder is the user's to get right.
  * Each label is placed at the same ground position on the loaded
    image, or at the same fraction of the image where georeferencing is
    missing. Positions are taken as given; a label is accurate to about
    half a pixel of the coarser image. Labeled Cycle (Shift+C) and Move
    are there to step through them and correct any that are off.
  * Masks are left out where the sizes differ, to be redrawn. The
    preview and the status bar say how many.
  * Everything else arrives unchanged: class, description, links, size,
    rating, review, heading.
  * With both the low-resolution folder and its mirror loaded, labels go
    onto the files they were made on.

No project format change - still 4.8.

## 2.3.10 (2026-10-02)

Labeled Cycle, moving a label, and marking the labels of the image shown.

  * Labeled Cycle (toolbar, or Shift+C): steps through the selected
    group's images that have labels - same order and keys as Cycle
    (Space next, Ctrl+Space back). The status bar shows how many of the
    group are labeled. A detour, to label another image say, comes back
    to the image it left.
  * Move a label: right-click a label > Move, then click where it
    should be. Right-click or Escape cancels. On its own image only the
    position changes; masks, orientation, size, rating and review stay.
    Where images overlap, a click on the label's own image stays on it.
  * A move onto ANOTHER image asks first - Move or Cancel. There the
    label joins that image, loses its masks, and keeps its true-north
    heading where both images are georeferenced. The question can be
    switched off in the box itself or with Labels > Warn Before Moving
    a Label to Another Image; it is on until switched off.
  * A move is one undo step, and who moved the label is recorded.
  * In Cycle, View Cycle and Labeled Cycle, every label on the image
    being shown wears the same white corner brackets, so labels of
    neighbouring images that overlap onto it can be told apart.

No project format change - still 4.8. Labels gain an optional
attribution key, "position".

## 2.3.9 (2026-10-01)

Remove Missing Images, and a left-hand column that folds away.

  * File > Remove Missing Images. An image that is not found, and that
    nobody goes looking for, used to stay in the project with every
    label that was on it - in each save and each ground truth export -
    with nothing on screen to remove it from. The new action lists
    those images with their label counts; the ticked ones are removed
    with their labels. The Locate Missing Images dialog offers it too
    (Remove From Project). It is one undo step: Ctrl+Z brings the
    images back in their places with their labels and links. The dialog
    warns when every image in the project is missing, which usually
    means a drive or share is not connected.
  * File > Locate Missing Images now opens from the menu. It only ever
    worked when offered on opening a project.
  * The sections left of the canvas - Layers, Labeled Images, Waypoints
    and Hard Negatives - open and close under a header with a chevron.
    A closed section is one row and gives its room to the others;
    reopened, it returns to the size it had. Which are closed is
    remembered between sessions. Show on map stays in the Waypoints
    header while that section is closed.
  * After removing layers or groups, an open Snippet Editor no longer
    lists the labels that were removed.

No project format change - still 4.8.

## 2.3.8 (2026-09-30)

Waterfall mode fixes, and a crash on exit.

  * After waterfall mode, zooming to a layer and switching it on could
    show nothing until the app was restarted. A zoom made after
    scrolling the stack was trapped far from its target, and every later
    zoom with it. Zoom to layer, zoom to label and the first-image fit
    now always arrive, and recover a view that was already stranded.
  * Holding Space glided off the end of the stack into empty canvas.
    The glide and panning now stop at the ends of the stack.
  * Waterfall left every image of the group switched on. A cycle
    afterwards hid nothing - overlapping images showed the same one at
    every step - and read the whole group at each step. Leaving the mode
    switches off what it switched on; leaving for Pan, Label or Ruler
    keeps the image that was in view.
  * W while already in waterfall was ignored. With another bottom-level
    group selected it now stacks that group; a new or opened project
    leaves the mode.
  * The place is kept between modes: a cycle or view cycle started after
    waterfall opens on the image that was in view, and waterfall entered
    from a cycle opens on the cycle's image.
  * The status bar no longer keeps a cycle or waterfall mode's message
    after the mode is left.
  * Undo and redo bring a far-off label fully into view.
  * Closing the app while snippet thumbnails were still loading could
    end in a crash: a read finishing after its list was gone is now
    dropped.

No project format change - still 4.8.

## 2.3.7 (2026-09-30)

Snippet Editor fixes, from a review of the 2.3.4-2.3.6 features.

  * Linked row: clicking a snippet the list was not showing (a view of
    the object in another class, or one the Show filter leaves out) did
    nothing. The list now switches to show the object - and the Show
    filter to All, when that is what hid it - and puts the snippet in
    hand.
  * Linked row: after a Display Settings change the row kept its old
    thumbnails. It reads them again with the rest of the editor.
  * Changing a whole linked object's class while the list shows one
    class could jump back to the top of the list (when the next snippet
    was the object's other view). The editor now lands on the next
    snippet still listed, as stepping would.
  * The linked row's heading counted labels as images; it now counts
    images, and says "N labels in M images" when two linked labels share
    an image.
  * The HDF5 export summary no longer counts a rejected label of a class
    the project no longer has as "left out as rejected" - it was left
    out for its class.
  * The Review buttons lose their Alt mnemonics: A, X and F are the keys.

No project format change - still 4.8.

## 2.3.6 (2026-09-30)

Snippet Editor: view one linked object.

  * The Class dropdown now lists, after All classes and each class, every
    linked object - named by its class (both, when its labels disagree),
    its group ID when it has one, the start of its id and how many labels
    it has, e.g. "vessel · Convoy · linked 3f2a1c9e (4 labels)".
  * Choosing one lists only that object's labels across every image. The
    Show filter, both views and the class picker work as usual, and each
    caption names its class.
  * The choice is kept while the list refreshes, and falls back to All
    classes if the object is gone.

No project format change - still 4.8.

## 2.3.5 (2026-09-30)

Class changes, group names, and who did what.

Class changes
  * The Snippet Editor's Label class picker takes a typed name: a name
    that is not a class yet becomes one.
  * "Also change linked labels" (ticked by default, remembered) decides
    whether the linked labels change too. Unticked, only the snippet in
    hand changes - for the part of an object that needs its own label.
  * Changes are in the project, so saves and ground truth exports carry
    them. One undo step, which also removes a class it created.

Group names
  * Every snippet in the Snippet Editor - the list, the Grid view and the
    linked row - shows the group folder it came from.

Who did what
  * A user name (no password), asked for on first start and changed with
    File > Switch User...; shown in the status bar.
  * Labels record who created them and who last set each value; masks
    record who drew them. The label's tooltip says who labelled it.
  * The groundwork for several people labelling the same objects and a
    reviewer comparing them, in a later release.

Project format 4.8: adds the optional per-label attribution and each
mask's author. Older versions of GeoLabeller open 4.8 projects, ignoring
them.

## 2.3.4 (2026-09-28)

Snippet Editor: review, class changes, and the linked object at a glance.

Review
  * A Review section marks each snippet accepted, rejected, or to be
    looked at again. Its worklist is Needs review against Reviewed.
  * In the Single view, A accepts, X rejects and F flags for another
    look, then the editor moves to the next snippet ("Advance after
    marking", on by default). Each mark can be undone.
  * Rejected labels stay in the project and the ground truth file, are
    drawn dimmed on the canvas, and are left out of the HDF5 and
    sub-image exports; both exports say how many were left out.

Class changes
  * The Snippet Editor has a Label class picker (Ctrl+1-9 picks by place
    in the list), and the canvas's label right-click menu has Change
    Class.
  * A class change applies to every label of the linked object - the
    same thing seen in different images.

Linked row
  * Under the Single view, every snippet of the object in hand side by
    side, with its class and badges. Differing classes are called out.
    Click one to work on it.

Project format 4.7: adds the optional per-label review. Older versions
of GeoLabeller open 4.7 projects, ignoring it.

## 2.3.2 (2026-09-28)

Undo and redo.

  * Edit > Undo (Ctrl+Z) and Redo (Ctrl+Y or Ctrl+Shift+Z). The menu
    names the step it would take.
  * On the canvas: placing, removing, linking and unlinking labels,
    descriptions, group IDs and hard-negative flags.
  * As one step each: Clear All Labels, removing a class with its labels,
    Import Ground Truth.
  * In the Snippet Editor: mask strokes (a step each), orientation,
    confidence and size. Ctrl+Z there uses the same history, so the order
    matches what was done across both windows.
  * The class, description and mask-name lists, and waypoints.
  * A gesture that touches several labels is one step - a box link, a
    heading or size shared to linked labels.
  * An undone or redone label off screen is brought into view, and
    undoing back to the saved state clears the unsaved mark.

Not undoable: adding or removing imagery, relocating images, display
settings. Up to 200 steps are kept for the session; saving keeps them,
opening or starting a project clears them.

No project format change - still 4.6.

## 2.3.1 (2026-09-25)

The installer, and a versions page.

Installer
  * The licence is shown once. Upgrading used to ask for agreement
    twice: the upgrade notice ran the licence page and Windows Installer
    then ran the stock licence page again.
  * Every install offers the folder page. Just-me installs - the default
    - used to go straight to the shortcuts with no way to choose where.
    The folder chosen is remembered, so an upgrade defaults to where the
    previous version was installed.
  * The shortcuts page is two tick-boxes, Start Menu and Desktop, both on
    by default, instead of the feature tree and its drop-down menus.
    Add/Remove Programs > Change shows the same page.
  * Installing for all users now lands in the 64-bit Program Files
    rather than Program Files (x86).
  * The pages run Welcome, Licence, Scope, Folder, Shortcuts, Install.

Help > About
  * Lists what the build is made of: the GeoLabeller version, Python and
    the operating system, the libraries it runs on with the native
    libraries they wrap (Qt, GDAL, PROJ, HDF5), where the install and the
    settings file are, and every bundled package. Copy puts it all on the
    clipboard for a bug report.

Build
  * The installer is frozen on Python 3.14, the version the test suite
    runs on. Windows is the only build target; the Linux build script
    is gone (Linux users run from a conda or virtual environment).

No project format change - still 4.6.

## 2.3.0 (2026-09-24)

Display Settings: choose how a group's imagery is drawn.

For imagery whose RGB composite is unreadable - where only one channel
holds a picture - a group can now be drawn differently.

  * View > Display Settings... (Ctrl+D), or right-click a group >
    Display Settings..., opens the window for that group.
  * Show one band as grayscale, or pick a band for each of red, green
    and blue - including a fourth band or beyond.
  * Per channel: brightness, contrast and gamma, over a histogram of the
    band with the curve drawn on it. Auto spreads the band's 2-98
    percentile range from black to white. In red/green/blue mode a
    channel can be hidden outright.
  * Changes show on the map, in the Snippet Panel and in the Snippet
    Editor as they are made. OK keeps them; Cancel puts back what was
    there.
  * Sub-groups use their group's settings unless they set their own.
    Removing a group removes its settings.
  * Settings are saved in the project. Display only: the HDF5 dataset
    and sub-image exports keep the original pixels.

Also: the automatic stretch for 16-bit and float imagery now covers
every band, not only the first three. Bands 1-3 look exactly as before.

Project format 4.6: adds the optional display_settings object. Projects
without display settings are written as before, and older versions of
GeoLabeller open 4.6 projects, ignoring the settings.

## 2.2.0 (2026-09-24)

Waypoint export and import.

  * Export > Waypoints (GPX)... writes every waypoint of the project to a
    GPX 1.1 file - the format GPS receivers, Google Earth, QGIS and GDAL
    read. Each waypoint keeps its name and its WGS84 position.
  * File > Import Waypoints... adds the waypoints in a GPX file from any
    producer, or the waypoints of another GeoLabeller project file, so a
    colleague's waypoints can be taken from their project directly.
  * Only what is new is added: a waypoint already in the project at that
    place, under that name or unnamed in the file, is left alone, so
    importing a file twice adds nothing the second time. Entries without
    a usable position are skipped and counted. A confirmation states the
    counts before anything changes.
  * Both entries are listed under Help > Keyboard Shortcuts, and the ICD
    (section 3.9) specifies the file.

No project format change - still 4.5. Nothing new to install.

## 2.1.2 (2026-09-24)

View Cycle cycles the selected group's images on screen, and keeps its
place.

Reported: "even if the group had 100 geotiffs, the view cycle would say
something like 'finished cycle 6/6'". View Cycle queued every layer whose
footprint touched the viewport - any group, hidden or not, in load order
- and zoomed to one image that is the handful of tiles overlapping the
margins. What the mode is for: the images of the CURRENT SELECTED GROUP
that intersect the current view.

  * V queues the selected group's images on screen, in tree order. An
    image selected in the tree stands for its group. The status bar reads
    "View Cycle: <group> - 6 of 100 on screen"; the end notice says
    "6 of 6 on screen".
  * A detour (Ruler, Pan, Label) resumes the run while the image it was
    on is still on screen. Pan away to another area and V starts there.
  * V at the end runs the same images again; once the view has moved, V
    takes the group's images now on screen. A finished run is not resumed
    after leaving the mode.
  * The cycle hides only images it switched on itself. A checked group
    keeps all its images through a run - in Cycle mode too.
  * C then V then C keeps both places: each cycle mode parks its own run.
  * An image removed from the project leaves the queue, live or parked,
    with the position kept. Removing the image on screen moves on to the
    next.
  * V mid-run with another group selected switches to it, as C does.

No project format change - still 4.5.

## 2.1.1 (2026-09-23)

Removing a group now deletes every image the project files under it.

Reported: after removing a group that contained labels, the labels were
still in the project, and still in every ground truth export. A shared
project opened on a machine that does not have some of the group's image
files gives those images no row in the layer panel - they wait for
Locate Missing Images - and "Remove" only ever deleted the rows the
panel had. The rest stayed, labels and all.

  * A group removal deletes everything the project files under that
    group, shown in the panel or not, sub-groups included. Membership is
    by path component: removing "mission" never takes "missionary".
  * The confirmation counts those images and their labels too, so it
    states what will really go.
  * An image whose path the project spells differently from the panel
    (another machine's project, another drive-letter case) is matched
    rather than skipped.

A project already saved with such leftover labels is unchanged by this;
opening it and removing the group again clears them.

No project format change - still 4.5.

## 2.1.0 (2026-09-21)

A label's length and width are measured in the Snippet Editor. A minor
version rather than 2.0.3, because a way of working moves: the map canvas
no longer puts a size on a label.

  * The Snippet Editor gains a Size section, with a Measure tool on the
    Single view. The first line drawn is the length, the second the
    width; each is stored as it is drawn. A further line corrects the
    WIDTH, never quietly the length, and either can be chosen to redraw
    (two radio buttons, or the L and W keys). A readout follows the drag;
    right-click or Clear takes both away. The lines stay on the snippet
    for the session; only the numbers are stored, as before.
  * Why there: a snippet is raw source pixels, zoomable to 32x, so a line
    can be placed far more precisely than on the reprojected map - and it
    is always the label's own image, which the map had to work out. The
    metres are the same metres: both ends go through the image's
    georeferencing to WGS84 and are measured on the ellipsoid, tested to
    agree with the map to within a millimetre. An image with no
    georeferencing cannot be measured, and says so.
  * The Show filter gains "Needs size / Sized" - sized means both - and a
    sized snippet wears its size in the list.
  * "Apply size to linked labels" replaces Options > Wire meas. to linked
    objects, and the Options menu went with its only item. On, every
    label of the same object gets the size: measure its best view once.
    Off, the default it always had, each view is measured on its own.
    Remembered per user; it also decides whether linking two labels
    shares an existing size.
  * The length line is not a heading. A label may be asymmetric, and
    nothing guarantees its heading runs along its length.
  * Gone from the map: right-click > Measure Length / Width, the M key
    and Clear Measurements. The Ruler stays - R, or Shift+drag from any
    mode, still reads a ground distance and touches no label. What a
    measured label looks like on the map and in the Labeled Images panel
    is unchanged.

Also: running the test suite no longer writes into the settings of
whoever runs it. File > Open Recent on a development machine had filled
with the tests' temporary projects. The app now makes its settings object
in one place, and a test run is sent to a throwaway folder; an installed
copy was never affected, and for a user nothing changes.

No project format change - still 4.5. F1 and the ICD describe sizing in
the editor.

## 2.0.2 (2026-09-20)

Imagery reaches the screen faster. Labellers wondered whether cycling
reverse-alphabetically cost them disk locality; measured, it does not -
forward and reverse step at the same speed, and the disk is about a
tenth of a load. What a load spends its time on is the reprojection into
the scene's projection, 80-85% of it at every level, and that ran on one
thread. A cycle step also waited on a timer it had no use for.

Measured through the application's own load paths, on 4000x4000 UTM
GeoTIFFs with overviews 2-32:

  cycle step, key press to picture      ~65 ms  ->  ~2.4 ms
  loading a fitted image                 125 ms  ->    76 ms
  full-resolution load (zoomed in)      1800 ms  ->   718 ms
  a viewport of zoomed-in detail tiles  1351 ms  ->   590 ms

  * A cycle step is on screen at once. It used to wait 50 ms on the
    throttle that coalesces panning and zooming, with its image already
    in memory. The update still runs after the zoom, never before it, so
    a step never loads a level it is about to discard.
  * Reprojection runs on several threads - about a quarter of the cores,
    at most four, since up to four loads already run at once. On four
    cores nothing changes. The pixels are identical.
  * GDAL decodes on several threads too. GDAL_NUM_THREADS is set only
    when the environment does not already define it, so an
    administrator's value wins.
  * An open skips GDAL's listing of its directory where that directory
    holds no sidecar files - the expensive part of an open on a network
    share. The import already did this; every level load, detail tile
    and snippet read did not. A folder holding any sidecar keeps its
    listing, so an external .ovr pyramid is still found; a sidecar added
    while GeoLabeller is running is picked up at the next start.

The order a cycle visits images in is unchanged.

No project format change - still 4.5. The ICD's recommendations for
source imagery gain the last two points.

## 2.0.1 (2026-09-20)

What labellers asked for after a week on 2.0.0, and one thing that was
in their way.

  * The Snippet Editor's keys now work wherever the focus is. Qt gives a
    key to whichever widget holds it, so what worked depended on the last
    thing clicked: after Add Mask, Space pressed that button again rather
    than stepping; the snippet list took Space but ate 1-5 as type-ahead;
    the number boxes took both; and the paint canvas takes no focus at
    all, so clicking the snippet being painted never helped. Space and
    Ctrl+Space step the list, and a section's keys reach it, from
    anywhere in the window - while a text field still keeps every key,
    since a mask name may hold spaces and digits.
  * A description preset's own key switches it off. Shift+2 selects,
    Shift+2 again goes back to none, so the left hand never reaches
    across the number row for Shift+0 - which meant letting go of the
    mouse, over and over. Shift+0 still works, and another preset's key
    still switches to it. Switching one now also flashes over the
    imagery, where the eyes are, rather than only in the status bar.
  * Every file dialog reopens where it was last used, remembered per
    kind: imagery, project files and exports apart, since they rarely
    live near each other. Adding the second directory of a survey no
    longer means walking the tree again.
  * File > Open Recent lists the projects opened or saved lately, newest
    first. An entry opens the project just as the dialog does, the
    unsaved-work guard included; one whose file has moved says so and
    drops off the list.

No project format change - still 4.5.

## 2.0.0 (2026-09-20)

The Snippet Editor: one window for everything recorded per snippet
(Labels > Snippet Editor...). It replaces the separate Mask Editor and
Orientation Editor windows, which are gone along with their menu items.

  * One snippet list - a class picker and a Show filter pairing "needs"
    and "done" for every section - feeds two views: Single, one snippet
    at a time and zoomable, with the Orient and Paint masks tools; and
    Grid, a page of snippets at once, where double-clicking one opens it
    in the Single view. Both follow the list's order.
  * A column of sections - Orientation, Masks, Confidence - each
    switching on and off, taking its panel, its pair on the Show filter,
    its overlay, its tool and its keys with it. Switched-off sections are
    remembered. The next value asked for is a new module and
    one line in that list.
  * Confidence: every label can be rated 1 (lowest) to 5 (highest) - the
    buttons, or keys 1-5 in the Single view, with 0 to clear. Stored per
    label; project format 4.5, which omits an unrated label's rating and
    reads anything other than a whole number 1-5 as unrated.
  * The HDF5 training export now also carries, aligned with images row
    for row: confidence (uint8; 0 unrated, and for every hard negative)
    and orientation_px_rad / orientation_deg (float32, NaN where there is
    none) / orientation_derived. Each group is written only when some
    exported sample has a value for it.
  * Space and Ctrl+Space step through the list; the save bar says whether
    the work is on disk yet, and Ctrl+S writes it. F1 and the ICD's
    keyboard reference document the window's keys.
  * Add Mask with no name typed says so under the name picker, where it
    can be seen. Hosted in the old window's footer, it said nothing.

Also in 2.0.0, from 1.10.1: File > Import Ground Truth... adds the labels
from a GT file made on another computer to the current project, matched
to the images loaded here by filename.

Everything recorded per snippet now lives in app/snippet_editor/, apart
from the main GUI and from the non-GUI logic the exporters share.

Project format 4.5. Older projects load unchanged.

## 1.10.1 (2026-09-18)

Import Ground Truth: another computer's labels, onto the images loaded
here.

  * File > Import Ground Truth... adds the labels from a GT file (Export >
    Ground Truth, or any project file) made on another computer to the
    current project - typically imagery loaded with Add Directory, where
    the same GeoTIFFs sit under different paths.
  * Each GT image is matched to a loaded image by filename (case-
    insensitive; GT files written on Windows or Linux alike), checked
    against the size and coordinate system the GT recorded. Several fits
    go to the longest shared folder path; a tie, or one image here
    claimed by two GT images, matches nothing rather than guessing.
  * A preview lists every GT image - Found, Not loaded here, Different
    file (same name, other size or CRS), Ambiguous - with its label
    counts. Nothing changes until Import is chosen.
  * Importing only adds. A label already in the project is skipped, so
    importing the same GT again adds nothing and an updated GT brings
    only its new labels. Labels keep their pixel positions and every
    field, and links arrive intact. Their classes and the GT's
    description and mask-name presets are added; a hard-negative flag or
    location tag is taken only where the local image has none.
    Waypoints are not imported.

Also: two diagnostic scripts under tests/, committed before that folder
was ignored, are no longer part of the repository.

No project format change - still 4.4. The ICD gains an Import ground
truth entry.

## 1.10.0 (2026-09-18)

Cycle mode, at its ends and between groups. A minor version rather than
1.9.10 because the behaviour visibly changes: the counter now counts up,
and the end of a group no longer ends the cycle.

  * Space on the final image stays on it, and Ctrl+Space goes back.
    Running past the end used to hide the image and discard the cycle,
    leaving nothing to step back through. Ctrl+Space on the first image
    likewise stays put.
  * Either end shows a small notice over the canvas - "End of group -
    300 of 300" - that takes no focus, lets clicks through and fades
    after 2.5 s. View Cycle too.
  * The counter counts up: 1/N on the first image, N/N on the final one.
    The order images are visited in is unchanged.
  * C in Cycle mode starts the next cycle without a detour through Pan:
    at the end of a group it starts that group again, and with another
    group selected it switches. A stray C mid-group is still ignored.
    Fixes a regression from 1.9.1. V restarts a finished View Cycle.
  * Starting any cycle hides the image the previous cycle left showing,
    including a restart of the same group. Layers switched on by hand are
    left alone, and leaving a cycle for another mode keeps its image on.

Shift+Space is unchanged: it steps forward. Ctrl+Space is the back key.
No project format change - still 4.4. The ICD's Cycle entry now covers
going back and the ends of a group.

## 1.9.9 (2026-09-16)

Allow Overlap in the mask editor.

  * A toggle beside Fill Enclosed decides whether a stroke may cover
    another mask's pixels. It is OFF by default and remembered per user.
    While it is off, a stroke stops at other masks, and Fill Enclosed
    treats them as edges: draw a shadow up to the hull and the hull's
    edge closes it, without the fill taking any hull pixel. Erasing is
    never blocked.
  * With it on, painting and filling behave exactly as in 1.9.8: the
    fill sees only its own mask's pixels as edges.
  * A hull drawn as an outline and left unfilled is not filled with
    shadow: with other masks as walls, a hole is filled only if it
    touches the mask being filled.

No format change - the project stays at 4.4. The ICD now says the
editor keeps masks exclusive by default, and that readers must still
expect overlapping masks, from projects painted with overlap on and
from older ones. Its mask editor row also covers the 1.9.8 additions.

## 1.9.8 (2026-09-15)

Six changes to the mask editor, from the feature requests, and the
project format moves to 4.4.

  * Closing with unsaved work asks first, and the editor says where the
    masks stand - stored in the project, saved to the file, or neither.
    The recovery snapshot follows a mask stroke within five seconds
    rather than up to a minute.
  * Its three panes are a splitter, and the widths are remembered.
  * Brightness and contrast for the VIEW only: masks and the
    object/background statistics still come from the raw imagery.
  * The snippet strip filters by All / Needs masks / Masked, with live
    counts, and retires a finished snippet when the user moves off it
    rather than out from under the brush.
  * Mask names are a project-level list (format 4.4, written only when
    non-empty), held as an ACTIVE name the way the main window holds an
    active class: pick it once, then Add Mask names, creates and selects
    the mask in one click, with no prompt anywhere in the path.

Format 4.4 adds one optional top-level array, so 4.3 readers are
unaffected and a project without presets serialises exactly as before.
ICD updated.

## 1.9.7 (2026-09-12)

Imagery that is already in Web Mercator now loads between 2.7 and 8.4
times faster, with no change to how anything looks or behaves.

Project format is unchanged at 4.3.

Imagery already in the display projection is no longer reprojected
- The viewer draws everything in one projection - Web Mercator - and
  every image was reprojected into it on every load. That reprojection is
  most of what a load costs: two thirds of it at the coarsest pyramid
  level, 94% at full resolution.
- An image that is ALREADY in Web Mercator needs none of it. Its own
  georeferencing places it exactly, and the viewer applies that while
  drawing, which it was doing anyway. Measured on 5000x5000 imagery:

      pyramid level   reprojected     already Mercator
           1/32          24.8 ms            9.3 ms      2.7x
           1/16          33.7 ms            6.3 ms      5.4x
            1/8          72.4 ms           11.2 ms      6.4x
            1/4         213.0 ms           29.6 ms      7.2x
      full resolution  3044.3 ms          361.9 ms      8.4x

  It applies to every read the viewer makes, including the windowed
  detail reads when you are zoomed in.
- Imagery in any other projection - UTM, a local grid - loads exactly as
  it did before, and looks identical. So does a project mixing several.
  Nothing needs configuring, and nothing needs converting: the saving
  simply appears for files that already match.
- If most of your work is on one survey, converting its imagery to Web
  Mercator once (any GDAL-based tool, or the built-in Optimize export
  with a reprojection step added) puts every load on the fast path. That
  moves the reprojection off the viewer and into a one-off conversion,
  at the cost of one generation of resampling. Labels, measurements and
  exports are unaffected either way: they are recorded against each
  image's own pixels and its own georeferencing, never against what the
  screen is drawn in.

Under the hood
- The display projection is a value the viewer carries rather than a
  constant buried in the loading code. Today it is always Web Mercator,
  which is what makes this release behaviour-neutral; making the viewer
  adopt a survey's own projection instead - and skip the reprojection for
  UTM imagery with no conversion at all - is the next step, and a larger
  one.

Tests: 969 -> 983.

## 1.9.6 (2026-09-11)

Clicking a label is about twelve times faster, and holds a sixteenth of
the memory, on ordinary survey imagery.

Project format is unchanged at 4.3.

Clicking a label
- Clicking a label zooms to ten metres of ground. To show those ten
  metres the viewer decoded and reprojected the WHOLE raster at full
  resolution: measured at 2,441 ms and 64 MB held for one 16 MP image -
  four kilometres of imagery decoded to show thirteen metres of it. That
  is now 203 ms and 4 MB.
- The viewer has always been able to read just the window it needs, but
  only used it for images too large to hold in memory at all - over 150
  megapixels. Any image bigger than a few megapixels now gets it: zoomed
  out, one cheap whole-image read carries the view exactly as before;
  zoomed in, only the part you are looking at is read.
- Opening an image no longer reads it twice when one read would do. The
  first read used to be a deliberately coarse preview, so something
  appeared while the real one loaded - worth it for a slow read, wasted
  on a fast one. Fitting an image among its neighbours went from twenty
  reads to eight, and from 0.30 s to 0.15 s.
- Every background read recomputed the image's full-resolution
  reprojection geometry, which never changes for a file. It is worked out
  once now and reused - about a third of the cost of a coarse read, paid
  for every image in view each time you zoom out.

On the report behind this
  This came from users reporting that imagery had been loading more
  slowly since about 1.7.x with the same files. Measured across 1.6.0,
  1.7.5, 1.8.4 and 1.9.5 on one fixture, every loading scenario is the
  same at every version - so nothing had slowed down. What changed in
  1.7.0 is that clicking a label began turning its image on and loading
  it, where before it moved the view to empty ground. The cost above was
  always there; 1.7.0 put it on the action people do most.

Tests: 953 -> 969.

## 1.9.5 (2026-09-11)

Imagery display: a crash, and the reason browsing a survey had become
slow. Everything here came from users reporting what they saw, and every
fix is measured against the case they described.

Project format is unchanged at 4.3.

Crash
- Importing a directory while another import was still running closed the
  application outright. The background loader forwards its worker's
  signals into itself, and those deliveries cross threads, so they are
  queued: superseding a loader cancelled it, waited for it and deleted
  it while deliveries were still on their way, and they arrived at
  something that was no longer there. Cancelling now stops the
  forwarding as well, which drops them with it. A second fault on the
  same path is fixed too: of the five signals the old loader disconnected
  on its way out, the fourth always failed, so the fifth was never
  reached and an abandoned loader stayed wired to the window for the rest
  of its life.

Imagery loads at the size it is being shown
  Four separate places decided how much of an image to decode, and all
  four ignored the zoom in some case. The rule is one rule now: load what
  the view can actually show, using the file's pyramid levels where they
  exist, decimating by halves where they do not, with the memory ceiling
  and the backdrop budget as limits.
- A large mosaic drew a coarse whole-image backdrop behind its detail
  tiles, and that backdrop was always about 4 MP however far out the view
  was. Sighting a survey from a distance decoded 4 MP per image to draw
  something twenty pixels wide. Measured on twelve images: first paint
  1.33 s to 0.56 s, and 109 MB of held pixels to 0.4 MB.
- Imagery with no pyramid was held at full resolution at every zoom. The
  first load of such an image was cheap, but every later one - and images
  are freed and re-loaded constantly while browsing - was the expensive
  one. Measured on a 36 MP file: 4,031 ms and 144 MB at full resolution
  against 174 ms and 2 MB at the level the zoom wanted. A twelve-image
  survey reloading after eviction went 6.63 s to 0.27 s.
- A pyramid stops where whoever built it stopped, and the view did too:
  zoomed out past the deepest level, that level was the coarsest on
  offer. Reading past the end costs little, since GDAL reduces from the
  coarsest level it has.
- Zooming out no longer re-reads a file to hold slightly fewer pixels.
  Crossing several zoom levels used to re-read the image at each one,
  each read replacing what was on screen with something blurrier, to free
  a few kilobytes a time. A re-read to coarsen now has to free something
  worth the read; a re-read to SHARPEN is never withheld.

Imagery loads in order again
- Whatever loads a group started at the front and worked through it,
  until 1.9.1 replaced the walk with a set for speed. A set has no order,
  and the loader takes work in the order it is given, so a group arrived
  scattered. The order is back, and the pass is as cheap as it was.

Tests: 914 -> 953.

## 1.9.4 (2026-09-11)

The code audit's 119 low-severity findings had never been worked. This
release closes thirteen of them, picked for what they actually cost:
three where the application silently produced a wrong number or lost
work, and the rest where it repeated work proportional to the whole
project rather than to what changed.

Project format is unchanged at 4.3.

Measurements that were wrong
- Measuring while the view moves. Clicking the start of a length line and
  then jumping the view - a double-click in the labelled panel, a
  waypoint, Zoom to Layer - measured the second click in the new frame
  against a start point left in the old one. Reproduced at 273 m for a
  line drawn across 100 m of ground, and 307 m for a width drawn across
  40. The length was stored on the label and written into the H5 export,
  with nothing on screen to suggest it. Measurements taken that way are
  worth re-checking.
- Label distances were computed on a sphere of mean radius while the
  orientation headings beside them were computed on the WGS84 ellipsoid,
  and the exported fields are named geodesic_*. The sphere reads short by
  0.12% at 10 degrees of latitude, 0.25% at 40 and 0.36% at 60. One
  ellipsoid now, shared by both.
- The metre rulers printed the waterfall stack's internal scene units
  under an "m" - fifty of them to a source pixel, so the grid was wrong
  by that factor throughout the stack. A raw-displayed image keeps its
  georeferencing, so the ruler reads real ground metres there now, and
  says "px" for an image that has no georeferencing at all.
- A coordinate pasted from a document with a typographic minus sign
  ("40.7536, -73.9832" as Word and most web pages spell it) had the sign
  DROPPED rather than rejected, so Go to Coordinates flew to the other
  hemisphere - Kazakhstan instead of Manhattan - and Add Waypoint stored
  it there.

Work no longer at risk
- Saving a project truncated the existing file before writing the new
  one, so a failure part way through - a full disk, a share that dropped
  - destroyed it. The "Failed to save project" message did not say that
  the file was already gone. It is written alongside and renamed into
  place now, the way the crash-recovery snapshot always has been.
- Closing the window no longer leaves the process running. The canvas's
  background pools were never stopped, and destroying them waits for
  everything queued, so closing during a prefetch of large mosaics left
  GeoLabeller alive with no window - a hang, as far as Task Manager is
  concerned - and a relaunch could race it over the recovery file.

Speed
- Unchecking a large group: hiding each image re-counted every image
  already held in memory. 5,000 images went from 1.72 s to 0.034 s.
- Opening a project checked every image path for existence three separate
  times, sharing nothing. Once now, and it reports progress while it
  works - on a network share at 20,000 images the old repeats were
  minutes of frozen window before loading began.
- Exporting masked snippets re-read every mask's run-length encoding for
  every crop it was asked about. 7,200 crop/mask pairs went from 6.95 s
  to 0.095 s; a densely masked image went from around 35 seconds to under
  one before any pixel is written.
- Panning a large project rebuilt the pan boundary from every image on
  every scroll: 13.6 ms a step at 20,000 images, gone.
- Removing a 5,000-image group went from 0.85 s to 0.010 s.
- Tiles that came back holding no data - the blank wedges of a rotated
  image, the empty side of a survey strip - were re-read every 50 ms
  while panning. They are remembered now, so the tiles that do have data
  are not queued behind them.
- Zooming a large image without a pyramid no longer builds hundreds of
  off-screen tiles the view has already left.

Tests: 796 -> 914.

## 1.9.3 (2026-09-11)

The code audit left 19 findings it never got round to adjudicating. All
19 have now been checked against the running application, three of them
reproduced by executing the code. Six turned out to be already fixed;
seven were real and are fixed here.

Project format is unchanged at 4.3. One thing to know before upgrading is
in "Image paths" below.

Data no longer lost or misplaced
- Image paths. Windows spells the same file two ways - the file dialog
  hands back "C:/dir/a.tif", a directory scan builds "C:\dir\a.tif" - and
  the project could not tell they were one raster. Add Directory on a
  folder and then Add Images on a file inside it produced TWO images and
  two layers for one file, with the "already loaded" check unable to
  match, labels split across both, and an export writing each twice.
  Paths are now settled to one spelling wherever they are stored or looked
  up. Projects saved before this are repaired as they load: two entries
  that turn out to be one file have their labels merged, keeping the
  location tag and hard-negative flag from either side.
  NOTE: this means a project saved by an earlier version may come back
  with its stored paths tidied (on Windows, "/" throughout). Nothing is
  lost, and the files it points at do not move.
- Measuring in the waterfall. A label is drawn on every stacked image it
  falls inside, and measuring one of those projections converted the drawn
  line through the WRONG image's georeferencing - so the length came out
  scaled by the ratio of the two images' resolutions. On a 1.0 m/px and a
  0.25 m/px pair, one drawn line measured 127.72 m or 510.87 m depending
  on nothing the user could see. Measurements are stored on the label and
  exported, so any taken this way are worth re-checking.
- Removing an image now clears the "linked" halo from the label left
  behind when its partner went with it.
- Flagging an unlabelled image as a hard-negative source filed it at the
  project root instead of in its group, where Set Location could not find
  it and a reload put it back in the wrong place.

HDF5 export
- The export is built from the images the canvas has loaded, so labels on
  an image that is not loaded - its file missing at open, or a relocation
  declined - could not be written, and nothing said so: a short dataset
  looked like a complete one. The dialog now counts them and says so
  before you export, and a per-object export names the views it cannot
  reach.
- Appending to a dataset whose columns disagree about how many samples it
  holds is refused, naming the column and both counts. Such a file can be
  left behind by an export that failed partway (a full disk); appending to
  it used to stack more rows onto the mismatch, and every sample after the
  break carried the wrong class.

Speed
- Placing a label on a large project no longer walks the labelled panel
  looking for its object: measured on 20,000 labels, 18.5 ms to add one
  became 0.19 ms, and is now flat at every project size. Removing one went
  24.4 ms to 1.6 ms.
- A background image load reads the file once instead of twice. Locally
  that is about 7% of a level change; on a network share, where opening a
  file is the expensive part, it is the whole of the saving - and it is
  paid per image every time you step through a cycle or zoom across
  levels.
- Zoom to Label now frames the ground extent it means to, at any latitude.
  It previously framed 7.7 m of ground where it intended 10 m at 40
  degrees north, and 5 m at 60 - tighter the further from the equator you
  worked, while Go To Location and Go To Waypoint were correct.

Robustness
- A failure inside the group preload no longer takes the application with
  it. An error in that background worker could end the process outright,
  with a progress dialog left on screen; it is reported now, and the run
  finishes.

Tests: 718 -> 796.

## 1.9.2 (2026-09-10)

HDF5 training export, rebuilt around the linked object. Project format is
unchanged at 4.3; the exported dataset gains one new column.

Export one object
- Right-click a label on the canvas, or an "Object: ..." row in the
  labelled panel, and choose Export Object Snippets... The export opens
  scoped to that linked object: its true-positive snippets from every
  image it appears in, or only from the layers toggled on.
- Instances keep their own class names. An object labelled "wreck" in five
  images and "debris" in two exports exactly as it was labelled, and the
  dialog shows the breakdown first - that split is usually a disagreement
  worth settling before the export rather than after.
- Several objects can be exported at once.

Choosing what an export contains
- The scope is now two independent questions instead of one list. Examples
  come from a selected object, the layers toggled on, all loaded layers,
  or nothing. Hard negatives come from nothing, the same images as the
  examples, the layers toggled on, flagged hard-negative sources only, or
  all loaded layers.
- Every previous scope is one of these pairs, and combinations that could
  not be reached before now can be: examples from everything with
  negatives only from the layers you have on, and negatives with no
  examples at all - which is how a set is topped up from a new area.
- The "also include flagged sources" checkbox is gone. It existed only
  because a single list could not say "examples here, negatives there".

Nothing labelled is ever a hard negative
- A negative was refused when it overlapped an example crop, and the
  example crops came from the labels being exported - so protection was a
  side effect of exporting rather than a rule. Exporting one object, or
  exporting negatives alone, would have offered every other object on the
  raster as background.
- Every image in a run now guards its full label list, whatever is being
  exported and whatever class the labels carry.

Know before you write
- The dialog counts what a run will contain as you change it: snippets,
  examples, hard negatives, the per-class spread and an estimated file
  size. It opens no imagery, so it keeps up with typing.
- At 64 px with 50% overlap a single 12k x 12k mosaic is about 140,000
  windows; the difference between a 2 GB file and a 200 GB one used to be
  one spin box with no warning.

New in the exported dataset
- object_ids, aligned with images: which linked object each example is a
  view of ("" for a hard negative). It is what lets a consumer group the
  views of one object, hold them all out of a split together - an object
  photographed by two overlapping surveys could otherwise sit in training
  and validation at once - and tell whether a file already holds an object
  before appending it again.
- Written only when a run has values for it, so files written by earlier
  versions still append cleanly.

Tests: 652 -> 718.

## 1.9.1 (2026-09-10)

The medium findings from the code audit, fixed. Project format 4.2 -> 4.3.

Data no longer lost or misplaced
- An image whose CRS has no EPSG code (a local grid, an ESRI-defined
  projection, a custom datum) now keeps its WKT as the new optional
  crs_wkt. Such images previously recorded an affine transform with no
  CRS at all: no lat/lon for their labels, no corners or geodesic fields
  in the export, no place on the zero-I/O load path, and no CRS for
  relocation to check a candidate file against.
- A mask snippet whose image will not open is read-only, and its masks
  are shown around the label rather than at the image's top-left corner.
  Edits used to commit there too.
- A drag in the layer tree only re-groups what was dragged. Any drop used
  to rewrite every non-georeferenced image's group, nesting another
  literal "Non-Georeferenced" level per save-and-reopen and changing the
  group written into the HDF5 export. Set Location works on those groups
  now as well.
- Combine Projects keeps waypoints, description presets and per-image
  flags from both inputs instead of silently dropping them.
- Autosave, crash-detection and group-preload failures are reported. They
  went only to print(), which the Windows build discards: a profile that
  had gone read-only meant crash recovery was dead for a whole session
  with nothing said.
- A project recorded on Linux and relocated on Windows (or the reverse)
  now finds every sibling image, not just the one file the user pointed
  at.

Correctness
- A sideways touchpad swipe no longer zooms the canvas out.
- Label markers are rescaled by every zoom, including each cycle step.
- Hidden markers are not click targets, so Remove Label cannot delete a
  label you cannot see.
- Re-pressing C in Cycle mode keeps your position instead of jumping to
  the end.
- The labelled-only JSON export states the format version it actually
  writes.
- Two images with the same name in one group export to two files.
- A layer cannot be dropped onto another layer and disappear.
- A mask whose encoding will not decode is kept and marked, not deleted
  by the next brush stroke.
- Large mosaics sharpen through every zoom level, and south-up or
  rotated rasters produce detail tiles at all.
- Imagery that cannot be loaded is reported once instead of retried
  twenty times a second.

Speed and memory, measured on a 20,000-image project
- Mouse move over the canvas: 2.70 ms -> 0.001 ms.
- One cycle step: 48 ms -> 2.4 ms.
- Directory import, per 50 ms slice: 23 ms -> 0 ms of tree walking.
- Orientation editor with 4,000 labels of one class: 10.3 s -> 0.11 s.
  It now pages 60 snippets at a time and can filter to unoriented only.
- Opening a saved project no longer decodes visible imagery at full
  resolution before choosing the level it actually needs.
- Waterfall mode releases each image's pixels on the way in and out, and
  images scrolled out of view answer to a memory budget.
- HDF5 export buffers by bytes, so large snippets no longer need
  gigabytes of RAM.
- Locate Missing Images shows progress instead of looking hung.

Tests: 495 -> 652.

## 1.9.0 (2026-09-10)

New: description presets. A project carries a list of preset
descriptions and an active one - toolbar picker, or Shift+1-9 with
Shift+0 for none - applied to every newly placed label, so labelling a
whole pass no longer means retyping the same note. Project format 4.2.

Changed: Remove on a layer or group now deletes those images and their
labels from the PROJECT, not just from the view, and says how many of
each before it does. Hiding without deleting is the visibility
checkbox. Ctrl+Shift+S goes back to Save Project As; the snippet
sidebar moves to Ctrl+B (both were dead, claiming one chord).

Fixed on Linux: ring colours use sha256, so the app runs on
FIPS-enabled systems without local patching.

The rest of this release is the remediation of a full-codebase audit -
48 findings closed across eleven commits, every one covered by a test
that fails without its fix:

  - Relocating a project's imagery no longer orphans its labels. A
    label deleted after relocation stayed in the file and came back on
    reopen; link, measure and describe silently did nothing.
  - Opening a second project in one window no longer fails with a Qt
    error, including re-opening the same project.
  - Edit Classes can remove a class again (it always raised), and the
    removed class's labels really go.
  - Clicks in a rotated raster's transparent margin no longer create
    labels with out-of-range pixels that the HDF5 export then drops.
  - Preload Group's Cancel reaches the worker while it is working.
  - Optimized GeoTIFF export handles JPEG-compressed sources.
  - A clean exit no longer reports a crash on the next launch.
  - Coordinate entry refuses input that names no position ("40N 75N"
    used to fly to Arctic Russia), and a corrupt mask RLE fails loudly
    instead of rendering shifted.
  - One nodata policy at every read: NaN and declared sentinels are
    both honoured, so all-NaN windows are not exported as samples,
    mask statistics no longer read "nan", and float imagery renders
    transparently rather than as a black block in waterfall.
  - The retired mosaic exporter is deleted (498 lines, no caller since
    b9092ba, two silent data-corruption bugs in it).

Faster at survey scale, measured on the 13k-image bench project:

  autosave snapshot        4.73 s -> 0.38 s   (0.41 -> 0.07 steady)
  Select all children      4.22 s -> 0.02 s   (2,000 layers)
  snippet strip refresh    1.61 s -> 0.00 s   (unchanged list)
  bulk visibility sync     6.76 s -> 0.01 s   (2,000 layers)

Test suite 369 -> 481.

## 1.8.4 (2026-09-09)

Adds a per-group location tag (right-click a group > Set Location...)
stored on the project's images - format 4.1 - and carried into the HDF5
export as a per-sample locations dataset. The HDF5 export gains a
float32 pixel format: native-unit grayscale values (float sources
unchanged, integer sources scaled to 0-1), refused on mismatched
appends. Float32 and 16-bit imagery now renders with a proper contrast
stretch on the canvas instead of clipping to black, shared with the
snippet views. Directory imports read headers about 3x faster
(estimated bounds, sidecar-aware GDAL opens).

## 1.8.3 (2026-09-08)

A click on a group checkbox made while a big group toggle is still
running now supersedes that toggle instead of running nested inside it
(the off click no longer waits for the on toggle to finish), and
sub-groups toggle as one batch. Opening a project whose imagery was
copied from another machine now relocates through one inferred prefix
rule with sampled verification - seconds instead of minutes - with a
live progress bar.

## 1.8.2 (2026-09-08)

Snippet masks are now stored as full-image RLE (project format 4.0):
entries anchor to the full source image, co-registered with the imagery,
with 3.9 windowed entries migrated automatically on load. The codec
streams runs end to end, so survey-sized images never materialize.

## 1.8.1 (2026-09-04)

Project loading overhaul - measured on a 13,000-image project, load
time drops from about 4 minutes to under 6 seconds (42x):

- Layers are constructed straight from the metadata the project file
  already stores (dimensions, affine transform, CRS): zero per-file
  header reads on load. Older entries without stored transforms load
  through the previous path and are backfilled, so one load-and-save
  upgrades a legacy project to the fast path permanently.
- The group-checkbox aggregate recomputes once per batch instead of
  once per added layer (was 94s of a 13k load on its own).
- Bulk layer adds are now linear: incremental z-ordering, and the
  async intake is time-budgeted instead of capped at 50 files/second.

## 1.8.0 (2026-09-03)

Snippet masks (project format 3.9):
- Labels > Mask Editor paints named binary masks on label snippets;
  masks may overlap (each is an independent layer). Live per-band
  raw mean/std of the masked object versus the background.
- Selectable snippet size (16-2048 px, freely typed); zoom to the
  cursor (0.25x-32x, crisp pixels) with Shift+drag panning; a
  wireframe preview of the brush's exact pixel footprint; Fill
  Enclosed floods a drawn outline (refused if the outline has a
  gap); sorted snippet strip with an All-classes view and
  Space / Ctrl+Space cycling.
- Stored on the label, run-length encoded as a compact string,
  anchored to source pixels (x0/y0 = the window's top-left in the
  full image); fully specified in ICD section 3.8.
- The ground-truth H5 export carries masks automatically in three
  aligned datasets (mask_pixels / mask_sample / mask_names),
  re-anchored per example crop so they overlay the imagery as-is -
  extraction needs nothing beyond h5py.

## 1.7.5 (2026-08-29)

- Waterfall glide speed control: while in waterfall mode, + doubles
  and - halves the glide speed (0.25x to 8x). The setting sticks for
  the session, the status bar announces each change, and the keys
  work with the layer tree focused too.

## 1.7.4 (2026-08-29)

Orientation editor:
- Oriented snippets carry a coloured border, so finished work reads
  at a glance.
- New toggleable option (on by default): drawing an orientation on a
  linked label gives the group's other labels the same TRUE-NORTH
  heading, each pixel angle re-derived through its own image's
  georeferencing. Propagated orientations show violet until drawn
  over by hand, and carry a persisted orientation_derived flag.
- Project format 3.7 -> 3.8 (optional orientation_derived).

Linking:
- Linked groups always wear a coloured halo ring around their
  markers: one colour per group, distinct between groups, updated the
  moment links change. The manual Show Linked step is retired.
- Right-click a label > Link by Box: drag a desktop-style box and
  every label inside is linked to that label's object.

## 1.7.3 (2026-08-29)

Waterfall mode overhaul:
- Leaving waterfall now lands the view on the geography of the image
  being examined (it used to stay parked over the emptied stack, and
  View Cycle then found nothing to start from).
- Length/width measuring works in waterfall: drawn lines are mapped
  through source pixels back to the ground, agreeing with geographic
  measurements to centimetres.
- The metre rulers no longer crawl sideways while gliding the stack.
- An unreadable stacked image no longer gets stuck in raw display.

Layer panel:
- Group checkboxes now mirror their layers after every change (on,
  off, or partial for a mix; clicking a partial box turns the whole
  group on). Waterfall/cycle entries, reveals and panel syncs used to
  leave them stale.

## 1.7.2 (2026-08-29)

- Build fix: the installer no longer nondeterministically ships the
  entire PyQt5-Qt5 wheel (~24MB of unused Qt - QML, software GL
  renderer, sql/geoservices/multimedia plugins). cx_Freeze bound the
  PyQt5 module to an arbitrary one of the three distributions sharing
  that package, ordered by randomized string hashing; 1.7.1 lost the
  coin flip. The freeze now pins the slim outcome and the build fails
  loudly if the bloat ever reappears.

Functionally identical to 1.7.1.

## 1.7.1 (2026-08-29)

- Fix: snippet sidebar items no longer open clipped to a thin strip
  until a resize; each item is sized for its icon up front.
- New: right-clicking an image on the canvas offers 'Unselect layer',
  hiding the topmost image under the cursor so the ones behind it
  come into view. Panel checkboxes stay in sync.

## 1.7.0 (2026-08-27)

- Snippet sidebar: every label as its exported pixels, filterable by
  class, linked-only, or a single linked group; zoom-to reveals and
  toggles on the source image.
- Orientation editor: draw a heading across snippets of a class;
  stores the unit-circle pixel angle (radians, (-pi, pi]) and the
  true-north heading (degrees CW, [0, 360)).
- Zooming to any label now always toggles its layer visible.
- Project format 3.7 (orientation_px_rad, orientation_deg).

## 1.6.0 (2026-08-21) - linked labels get a shared name

Linked labels can carry a group_id: a human-readable name for the linked
group, the readable companion to object_id's UUID. It is one value for the
whole group by contract - setting it on any member applies it to every label
sharing the object_id, linking spreads it, unlinking strips the leaver, and
a file that arrives disagreeing with itself is repaired on load. Edited from
the label's right-click menu; shown in marker tooltips; written to the
project file (format 3.6) only when set, so older files load unchanged.

## 1.5.2 (2026-08-21) - upgrades can no longer lose files

A 1.4.0 -> 1.5.1 upgrade in the field reported success while leaving
seventeen runtime files uninstalled, and the application could not start.
The installer now copies every file unconditionally on install, repair and
upgrade, so no overwrite comparison can ever decide a file need not be laid
down. Identical application code to 1.5.1; anyone whose upgrade broke can
simply install this over it.

## 1.5.1 (2026-08-21) - projects travel between machines

A project opened on a machine where the imagery lives under different paths
now finds its images again. Imagery that travelled with the project file
resolves silently on load; anything still missing gets a relocation dialog -
search a folder by filename, or locate one file by hand and its old-versus-
new path prefix relocates every sibling. File > Locate Missing Images offers
the same any time.

Matching refuses to guess: candidates are verified against each image's
recorded size and CRS, twins are settled by path agreement or not at all,
and one file can never satisfy two missing images. Labels, descriptions and
hard-negative flags ride along; new paths persist when the project is saved.
Projects authored on one operating system relocate correctly on the other.

## 1.5.0 (2026-08-21) - the loading engine, overhauled

An adversarially verified audit of the canvas threading and loading
machinery, implemented in full:

- Stale queued load events can no longer act on newer state: results are
  matched to the load actually in flight, Free Group cancels before it
  frees, and freed data stays freed.
- Motion stays fed: holding a waterfall glide or dragging a pan no longer
  starves tile dispatch, coarse tiles build centre-out between paints
  instead of freezing the window in one pass, and cycle steps evaluate
  detail at the zoom they land on.
- The loaders shed over half their transient allocations (a 1.2 GB
  temporary among them) without changing a pixel, and superseded loads now
  abandon mid-read instead of running to completion.
- Directory import no longer re-opens every file on the UI thread, and
  hidden layers fall under the warm-pool memory budget instead of
  accumulating until the window closes.

Also: "hard_negative" is reserved as a class name (the export writes its
own class by that name), and the last user-facing "GeoLabel" texts - the
About box, recovery prompt and file dialogs - now say GeoLabeller.

## 1.4.1 (2026-08-21) - hard-negative source images

Right-clicking an image on the canvas can flag it as a hard-negative source:
it contains confusers but no true positives. Flagged images collect in a new
Hard Negatives section of the layer panel, and the H5 export's labels-only
scopes gain a checkbox that slides them in whole as gt=False hard negatives,
sharing the train/validate/test split settings. Project format 3.5; the flag
is written only when set and older files load unchanged.

## 1.4.0 (2026-08-18) - the specification ships with the build

Help > ICD opens the Interface Control Document from the install directory.
Anyone running an installed copy can now read the specification of the project
files and exports they are producing, without the repository and without a
network connection, and the copy they get always matches the build they are
running.

Includes 1.3.5's fixes: waypoints no longer disappear underneath imagery,
crash recovery no longer drops them, and labels can carry a per-label
description that linking does not share.

## 1.3.5 (2026-08-18) - waypoint and label fixes

Waypoints no longer disappear underneath the imagery. Overlay z-values were
stamped once against a ceiling that rose with every image loaded, so a
waypoint drawn early in a session ended up below layers added later. Overlays
are now stacked as a group the layer count cannot reach, which fixes the go-to
location marker for the same reason.

Crash recovery no longer loses waypoints. The autosave snapshot built its own
copy of the project data and was never taught about waypoints, so recovering
restored the labels and silently dropped the rest. Both paths share one
serialiser now.

Labels can carry a description: free text on one label, shown as its tooltip,
and deliberately not copied to labels linked to it. Project format 3.4; files
written by earlier versions load unchanged.

## 1.3.4 (2026-08-17) - the window says which build it is

The main window titles itself "GeoLabeller v1.3.4" rather than "GeoLabel", so
the running build can be identified without checking Add/Remove Programs. The
number comes from the same VERSION file the release is tagged against, in a
frozen build as well as from source.

No 1.3.3 was released.

## 1.3.2 (2026-08-17) - a working installer, and a new one

Fixes a startup crash: 1.2.1 and 1.3.0 were frozen without the stdlib html
module, which app/shortcuts.py imports and app/main_window.py pulls in at
module scope, so the installed application exited with ModuleNotFoundError
before showing a window. Anyone on 1.3.0 needs this. The build now refuses to
freeze an application that imports a module the packaging excludes.

The MSI is now built with WiX, and asks during setup what the previous one
decided when it was built: install for everyone or just you, which shortcuts
to create, and where. An existing install is reported and named before it is
replaced, and the licence page shows the project's own licence. Installs made
by the old packager upgrade to this one.

There is no 1.3.1 release; its build failed on a bug in the packaging script
and nothing was ever published under that tag.

## 1.3.1 (2026-08-17) - a working installer, and a new one

Fixes a startup crash: 1.2.1 and 1.3.0 were frozen without the stdlib html
module, which app/shortcuts.py imports and app/main_window.py pulls in at
module scope, so the installed application exited with ModuleNotFoundError
before showing a window. Anyone on 1.3.0 needs this. The build now refuses to
freeze an application that imports a module the packaging excludes.

The MSI is now built with WiX, and asks during setup what the previous one
decided when it was built: install for everyone or just you, which shortcuts
to create, and where. An existing install is reported and named before it is
replaced, and the licence page shows the project's own licence. Installs made
by the old packager upgrade to this one.

## 1.2.1 (2026-08-17) - cleanup and a crash fix

View > Clear Coordinate Marker raised NameError every time it was used: the
status message it built referenced variables that did not exist in that scope.

Removes the view-rotation feature, which nothing has called since Image Cycle
was replaced by Waterfall, along with ten uncalled methods and several unused
imports and attributes. Pyflakes is clean across the application.

The coordinate readout no longer scans every layer twice on each mouse move -
three times over blank ground - which is felt with a large directory loaded.

Background level loads are now guarded whole, so a failure is reported rather
than dying untraced in a worker thread and leaving the loading spinner turning
for a load that had already given up. Both this and the tile loader now
survive the canvas being destroyed mid-load.

## 1.2.0 (2026-08-17) - very large mosaics

Loading a very large GeoTIFF used to freeze the window and then take the
process with it. Adding a layer reprojected the whole image at full
resolution, synchronously, on the UI thread - roughly 37 GB of RGBA for a
100k x 100k mosaic, and over 100 GB at peak.

Layers now load lazily, so adding one returns immediately and the pixels
arrive in the background. A memory cap keeps any whole-image level within
reach, applied inside the loader so no path can bypass it. Beyond that,
images too large to hold whole keep a cheap coarse backdrop and read the
detail the zoom asks for one tile at a time, straight from the file: cost
follows the viewport rather than the image, so full resolution is available
at any zoom.

Measured on a 900 MP mosaic: adding a layer went from 6.9 s of frozen UI to
20 ms, and a 1600x1000 viewport at full resolution costs a few megabytes
where holding that level whole would have needed 3.7 GB.

Ordinary imagery is unaffected and keeps the simpler whole-image path.

## 1.1.0 (2026-08-17)

No notes were written for this release.

## 1.0.0 (2026-08-15)

No notes were written for this release.
