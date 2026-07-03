# TODO: Pyramid-Aware (Overview) Loading for Faster Rendering

Goal: take advantage of the GeoTIFF pyramid levels (overviews) produced by
`tests/generate_large_high_res_tiff.py` so large images render quickly instead
of always decoding at full native resolution.

## Background — how loading works today

All relevant logic lives in [`app/canvas.py`](app/canvas.py):

- `TiledLayer.ensure_loaded()` (~L153) triggers a full load on first display.
- `TiledLayer._load_and_reproject()` (~L166) reads **every band at full
  resolution** (`src.read(1)`, etc.), reprojects to Web Mercator, and stores the
  entire RGBA array in `self._rgba_data`.
- `TiledLayer._load_pixel_data()` (~L296) does the same for non-georeferenced
  images (`out_shape=(height, width)` = native size).
- `TiledLayer.create_tile_pixmap()` (~L425) slices `self._rgba_data` into
  `TILE_SIZE` (512px) tiles — **one fixed resolution regardless of zoom**.
- `MapCanvas._update_visible_tiles()` (~L1019) builds/removes tiles for the
  current view; `wheelEvent()` (~L1204) + `_schedule_tile_update()` re-run it on
  zoom/pan.
- Overviews (`src.overviews(1)`) are **never queried**. A 10000×10000 RGB image
  costs ~400 MB in memory and forces ~400 full-res tiles when zoomed out.

The subtasks below are ordered from lowest-risk/quick-win to more involved.
Each is intended to be independently committable.

---

## Phase 1 — Discover overview metadata

- [x] **1.1** In `TiledLayer`, add fields to hold overview info:
  `self._overviews: list[int]` (decimation factors) and
  `self._src_level_dims: list[tuple[int, int]]`.
- [x] **1.2** In the `rasterio.open(...)` blocks of `_load_and_reproject` and
  `_load_pixel_data` (and the bounds-only loaders), read
  `src.overviews(1)` and populate the new fields. Handle the empty-list case
  (no overviews → current behaviour). *(Added `_read_overview_metadata(src)`
  helper, called from all four loaders.)*
- [x] **1.3** Add a tiny helper `TiledLayer.has_overviews() -> bool` and a debug
  log line reporting discovered levels, to verify the test images expose
  `[2, 4, 8, 16, 32, 64]`. *(Verified against `tests/test_imgs/image_000.tif`.)*

## Phase 2 — Compute the target level from zoom

- [x] **2.1** Add `MapCanvas._scene_units_per_pixel() -> float` using
  `self.transform().m11()` (already used at ~L1586). This is meters-per-screen-
  pixel in Web Mercator. *(Returns `1/m11`, the reciprocal of view-pixels-per-
  scene-unit.)*
- [x] **2.2** Add `TiledLayer.select_overview_level(scene_units_per_pixel)` that
  returns the coarsest decimation factor whose effective ground resolution is
  still finer than one screen pixel (i.e. pick the smallest overview that keeps
  detail without over-reading). Return `1` when zoomed in fully or when no
  overviews exist.
- [x] **2.3** Unit-check the mapping with a few zoom values so level selection is
  predictable before wiring it into rendering. *(Verified on
  `tests/test_imgs/image_000.tif`: native≈0.01 m/px → level 1; 3×→2, 10×→8,
  50×→32, ≥200×→64.)*

## Phase 3 — Read from overviews instead of full resolution

- [x] **3.1** Non-geo path first (simplest, no reprojection): change
  `_load_pixel_data` to read at a decimated `out_shape` matching the selected
  level. A smaller `out_shape` makes GDAL/rasterio serve data straight from the
  nearest overview. Update `self._width/_height/_n_tiles_*` to the level dims.
  *(Now takes a `level` arg; reads `src.width // level` sized data.)*
- [x] **3.2** Geo path: in `_load_and_reproject`, read the source bands at a
  decimated `out_shape` (overview) *before* `reproject`, and shrink the
  destination `width/height` accordingly via `calculate_default_transform(...,
  dst_width=, dst_height=)`. Keep `self.bounds` in Web Mercator unchanged.
  *(Uses `src.transform.scale(...)` for the decimated source transform; bounds
  verified stable across levels 1/8/64.)*
- [x] **3.3** Track the currently loaded level in `self._loaded_level` so callers
  know which resolution `self._rgba_data` represents. *(Also added stable
  `_full_width/_full_height` so `select_overview_level` compares against native
  res even after a decimated load. `ensure_loaded(level=None)` keeps the current
  level and reloads only when it changes.)*
- [x] **3.4** Verify `create_tile_pixmap` still works: tile geo-extent math in
  `_update_visible_tiles` divides geo span by pixel span, so reduced pixel dims
  should self-correct the scale. Confirm no coordinate drift. *(At level 8 the
  grid drops from ~20×20 to 3×3 tiles; pixmap renders 512×512 and the loaded
  level is preserved through `create_tile_pixmap`.)*

**Measured (10000×10000 test image):** full load 11.35 s → level 8 0.20 s (57×)
→ level 64 0.013 s (870×). Behaviour unchanged in the app so far because nothing
requests a non-1 level yet — Phase 4 wires zoom → level.

## Phase 4 — Switch levels on zoom (level-of-detail)

- [ ] **4.1** In `_update_visible_tiles`, before creating tiles, compute the
  desired level via Phase 2 and compare with `self._loaded_level`.
- [ ] **4.2** When the desired level differs, reload `self._rgba_data` at the new
  level and clear existing tiles (`layer.tiles`) so they regenerate at the new
  resolution. Reuse `free_data()`/`ensure_loaded()`-style plumbing.
- [ ] **4.3** Debounce/guard against thrashing: only switch when the level
  actually changes, and keep the existing 50 ms `_schedule_tile_update` timer.
- [ ] **4.4** Add a "true ground resolution" value for the canvas view, in
  **meters/pixel**. `_scene_units_per_pixel()` returns Web Mercator metres per
  pixel (`1/m11`), which the existing scale bar uses directly. Web Mercator
  inflates real-world distance by `1/cos(latitude)`, so the *actual* ground
  resolution is `scene_units_per_pixel * cos(lat_center)`, where `lat_center`
  comes from `_web_mercator_to_wgs84()` of the view-centre.
  - Add `MapCanvas.view_ground_resolution() -> float` returning that corrected
    value (falls back to `_scene_units_per_pixel()` when no geo layer / at the
    equator, where the factor is ≈ 1 — as with the Null Island test images).
  - Optional: feed this corrected value into `_update_scale_bar` so the scale
    bar is accurate away from the equator, and/or expose it for level selection
    if we want LOD keyed to true ground resolution rather than raw scene units.

## Phase 5 — Responsiveness & memory

- [ ] **5.1** Progressive load: on first display, load the coarsest overview for
  an instant preview, then refine to the target level. (Optional but high impact
  for the 10000×10000 images.)
- [ ] **5.2** Move overview reads off the UI thread (extend the existing
  `AsyncFileLoader`/`QThread` pattern in `app/canvas.py`) so level switches don't
  freeze panning.
- [ ] **5.3** Free full-resolution data when zoomed out (call `free_data()` when a
  coarser level is active) to cap memory per layer.

## Phase 6 — Validation & fallback

- [ ] **6.1** Generate a test set: `python tests/generate_large_high_res_tiff.py
  --output-dir tests/test_imgs --num-images 5`, load them, and measure first-
  paint time + memory before vs. after.
- [ ] **6.2** Confirm graceful fallback for images **without** overviews (must
  behave exactly like today).
- [ ] **6.3** Confirm label placement, scale bar, and coordinate readouts remain
  pixel-accurate across level switches (these read `self.transform()` and layer
  bounds, which stay in Web Mercator).

---

### Notes / gotchas

- Reading a smaller `out_shape` than native is the simplest way to pull from
  overviews; alternatively open a specific level with
  `rasterio.open(path, OVERVIEW_LEVEL=i)`.
- `self.bounds` and all geo coordinates must stay in Web Mercator at every level;
  only pixel dimensions (`_width`, `_height`, `_n_tiles_*`) change per level.
- Keep `TILE_SIZE` fixed; the number of tiles shrinks naturally at coarser
  levels, which is where most of the speedup comes from when zoomed out.
