# Voronoi Pocket – Fusion 360 Add-In TODO

Goal: the user picks a flat (planar) face, sets a few parameters, and the add-in
draws a Voronoi cell pattern as a sketch on that face, then cuts every cell into
the body to a set depth. All of it happens in one command and one undo step.

## Where it stands

Version 0.6.0. The add-in runs in Fusion on Windows: the dialog, the sketch and
the cut work on a rectangular plate, an L-shaped plate with a hole, and curved
parts with holes, with an even border on all of them. Tests outside Fusion:
geometry 49, stress 24, Fusion-side logic 31 and a walk through the dialog 3
(the last two against a stand-in for Fusion's API).

- [ ] **Version 0.6.0 has not run in Fusion yet.** New: the closed loop (section 11.1). With stress on, the pattern is checked on the plate with its pockets cut and the walls are corrected, up to "Iterations" times (Stress tab, default 3). It runs on OK, and in the preview only with "Check in preview" ticked. Expect OK to take 5 to 30 s longer with it; set Iterations to 0 to switch it off.

- [x] Version 0.4.0 seen working in Fusion (from the log, 12 Nov): the two tabs, held and loaded edges, the stress calculation (2,519 points in 1.9 s), the coloured preview without errors, plate thickness (0.5 cm read), and OK with stress on (81 pockets, sketch and cut in 2.3 s).
- [x] Yield strength was not read in 0.4.0: Fusion reports it as `207000.0 [Kilopascal]` and its own unit converter refuses that spelling. Fixed in 0.5.0 with a list of the unit names Fusion uses.
- [x] Version 0.5.0 seen working in Fusion (from the log, 12 Nov 14:01–14:05, no errors):
  - [x] Yield strength filled in from the material: 207 MPa for Steel, 503 MPa for Aluminum 7075
  - [x] One load point picked, 500 N, turned to 130° with its handle; stress worked out with it together with an edge force, nine times
  - [x] Status line shows the lowest safety factor of the solid plate (from 22.3 down to 1.7 as the force went up)
  - [x] OK with stress on: sketch and cut in 1.7 to 4.5 s
  - [ ] Not visible in the log, so still to confirm by eye: the arrows, the direction handle sitting at the point, the Safety factor colours, and more than one load point
- [ ] Every change of a force, a direction, an edge or the thickness works the stress out again, about 2.6 s each time (2,512 points). Turning a direction handle therefore moves in steps. Options: a mesh-fineness setting, or the compiled helper (section 9).

- [x] First run inside Fusion
- [x] Cut went out of the body instead of into it: fixed (direction is taken from the face, and a cut that removes nothing is undone and flipped)
- [x] Uneven border on curved and non-convex faces: fixed and seen working in Fusion
- [x] Version 0.2.0 seen working in Fusion: depth arrow, simple mode (118 pockets at 50%), no errors in the log
- [x] Dragging the depth arrow froze Fusion for 40–50 s. Cause, from the log: each preview took about 7 s (maths 1.3 s, Fusion drawing the sketch 3 s, Fusion cutting 2.5 s) and a drag queues several. Fixed in 0.3.0 by not building the sketch and cut for the preview at all.
- [x] Version 0.3.0 seen working in Fusion: the outline preview is instant (0.5 s for a new pattern, of which 0.02 s is drawing), the depth parameter `VoronoiDepth` was created, no errors in the log
- [x] OK still freezes for 10–20 s by the user's count. The add-in's part of that was 5.5 s in the log (Fusion drawing 1,805 sketch curves 2.9 s, Fusion's Extrude cut 2.5 s); the rest happens in Fusion after the add-in returns. **Decision (user): the cut is Fusion's own Extrude, leave it as it is.**
  - [x] 0.3.1: the sketch is hidden after the cut, as Fusion's own Extrude does
  - [x] 0.3.1: the log gets a "dialog closed" line, so the time Fusion spends after the add-in returns can be read off
- [ ] One run gave 121 profiles for 120 pockets. Not explained: a search of 6,038 generated pockets found none that cross themselves. 0.3.1 saves the face outline of the last OK to `VoronoiPocket\voronoi_last_face.json` so the case can be rebuilt outside Fusion.
- [ ] Changing `VoronoiDepth` in Modify → Change Parameters moves the pocket floors (not confirmed yet)
- [ ] Try the package on a Mac (`dist\VoronoiPocket-0.6.0.zip`). Nothing in the code is Windows-only, but it has never run on macOS.

Where the time goes now (about 120 pockets on a curved part with holes):

| Step | Before | Now |
| --- | --- | --- |
| Dragging the depth arrow, per step | about 7 s | pattern reused, outlines redrawn in about 0.02 s |
| Changing a pattern value, maths | 1.1–1.8 s | 0.4–0.7 s simple mode, under 0.1 s otherwise |
| OK: Fusion draws the sketch and cuts | about 5.5 s, plus Fusion's own finishing | the same, once |

The last row is Fusion's own work on its own objects, so no outside program
(Rust or otherwise) can take it over. A Rust helper would only speed up the
middle row, and would need a separate build for Windows, Intel Macs and Apple
silicon Macs. Not done; see section 9.

Every preview and run is logged to `VoronoiPocket\voronoi_pocket.log`, errors
with their full traceback. To load changed code, switch the add-in's Run off
and on again (Shift+S), or restart Fusion.

Run the tests (no extra packages needed):

```
py VoronoiPocket\tests\test_geometry.py
py VoronoiPocket\tests\test_stress.py
py VoronoiPocket\tests\test_bridge.py
py VoronoiPocket\tests\test_dialog.py
```

---

## 0. Project setup

- [x] Add-in skeleton (hand-written, same shape as Fusion's Python template)
- [x] Folder layout:
  ```
  VoronoiPocket/                  # this is the folder Fusion loads
    VoronoiPocket.py              # run() / stop() entry point
    VoronoiPocket.manifest        # id, version, description, supportedOS
    config.py                     # command IDs, panel ID, default values
    commands/voronoiPocket/
      entry.py                    # toolbar button, dialog, event handlers
      resources/                  # toolbar icons
    core/
      geometry.py                 # PURE PYTHON – no adsk imports (unit-tested)
      stress.py                   # PURE PYTHON – flat stress calculation, stress-driven walls
      fusion_bridge.py            # face -> 2D outline, outline -> sketch, cut
      settings.py                 # remembers the last dialog values
    tests/
      test_geometry.py
      test_stress.py
      test_bridge.py              # bridge logic against a stand-in for the API
      test_dialog.py              # builds the dialog and clicks through it, same stand-in
    README.md                     # install (Windows and macOS) and how to use
  tools/
    make_icons.py                 # redraws the toolbar icons
    make_package.py               # builds dist/VoronoiPocket-<version>.zip
  dist/                           # the zip to hand to someone else
  ```
- [x] Manifest (version, description, `supportedOS: windows|mac`). The `author` field is still empty.
- [ ] Set up debugging in VS Code (Scripts and Add-Ins → Debug)
- [x] Standard library only. **No scipy, shapely or pyclipper.** They are compiled packages and break inside Fusion's embedded Python.
- [ ] `git init` and add a `.gitignore` (`__pycache__/`, `.vscode/`, `settings.json`)

## 1. Command and UI

- [x] Command definition with icon and tooltip
- [x] Button in **Solid → Create**, removed again in `stop()`
- [x] Dialog:
  - [x] **Face**: planar faces only, exactly one
  - [x] **Cells**: 1–1000, default 60
  - [x] **Seed**: the same seed gives the same pattern
  - [x] **Wall thickness**
  - [x] **Border margin**
  - [x] **Corner radius**: 0 = sharp corners
  - [x] **Smoothing**: 0–20 passes (Lloyd's algorithm)
  - [x] **Min pocket width**
  - [x] **Cut pockets** checkbox (off = sketch only)
  - [x] **Pocket depth**: value box plus a drag arrow on the model, pointing into the body (`DistanceValueCommandInput`). It appears once a face is picked.
    - [x] Dragging the arrow no longer rebuilds anything in the design (outline preview, section 6)
  - [x] **Simple mode** checkbox (on by default): shows only Border margin, Pocketing % and Seed
  - [x] **Preview** drop-down: Outlines (fast, the default), Real cut (slow), Off
- [x] Remembers the last-used values between sessions (`settings.json` in the add-in folder)

## 2. Geometry core (`core/geometry.py`, pure Python)

- [x] Point generation inside a 2D polygon:
  - [x] Uniform random, seeded
  - [x] Rejection sampling, so points land only on the face and not in holes
  - [ ] (Later) Poisson-disk sampling for more even spacing
- [x] Voronoi cells by half-plane clipping
  - [x] Each cell starts as the bounding box
  - [x] Cut by the bisector with each neighbour, nearest first
  - [x] Stops once a neighbour is more than 2× the cell's reach away
  - [ ] Only if speed becomes a problem: Bowyer–Watson Delaunay, or a grid to find neighbours faster
- [x] Lloyd relaxation (cells are trimmed to the usable area first, so points spread out evenly up to the border and around holes)
- [x] Wall thickness: every cell shrinks inward by `wall / 2`; collapsed cells are dropped
- [x] Boundary handling: the whole face is shrunk by the margin (outline moves in, holes grow, inward corners become round) and every pocket is trimmed to that line. The border band is exactly `margin` wherever a pocket reaches it, on any outline.
  - [x] `offset_region`: shrinks outline and holes, handles necks that pinch off and thin parts that vanish
  - [x] `clip_to_region`: trims a convex cell to the shrunk face; can give several pieces
  - [x] A cell that would surround a whole hole is split in two through the hole, so no pocket is ring-shaped
  - [ ] A hole shaped like a "C" that closes up when grown loses the island inside it (no pockets there). Rare; fix if it shows up.
- [x] Corner rounding, exact: everything is shrunk by the radius first, then the result is grown back with arcs (`grow_loop`). Every outward corner has exactly that radius, also where a pocket meets the border line. Cells too small for the radius are left out.
  - [ ] Where a pocket follows a curved edge it is drawn as many short straight lines (within 0.02 mm of the curve). Draw these as arcs or splines if the facets show.
- [x] Min-size filter (a circle of the minimum width must fit in the pocket)
- [x] Simple mode (`build_pattern_for_share`): given only the margin and the share of the face to pocket
  - [x] Wall thickness is searched for until the pockets cover the asked share (within 0.2% of the face)
  - [x] Cell count is the one where those walls come out about as wide as the border margin, between 5 and 120 cells
  - [x] Corner radius is 8% of the cell size; pockets under 25% of the cell size are left out
  - [x] If the share does not fit inside the border band, the result is the most that fits and says so
  - [ ] The 120-cell limit is there for Fusion's speed. Low percentages on big parts hit it and get walls thicker than the border. Raise it if Fusion turns out fast enough.
- [x] Plain `(x, y)` tuples in and out, testable outside Fusion

## 3. Fusion bridge: face → 2D outline  *(written, not yet run in Fusion)*

- [x] Check the selected face is planar
- [x] Sketch created with **`sketches.addWithoutEdges(face)`**. Plain `add()` projects the face edges, which creates an extra outer profile.
- [x] Outer loop and holes read from `face.loops`
- [x] Edges turned into points with `edge.evaluator.getStrokes(...)` (handles arcs and splines); edges are stitched by position, so their order and direction don't matter
- [x] Points converted with `sketch.modelToSketchSpace()`
- [x] Faces inside sub-components: the proxy is unwrapped (`nativeObject`) and everything is built in the face's own component

## 4. Sketch creation  *(written, not yet run in Fusion)*

- [x] `sketch.isComputeDeferred = True` while drawing
- [x] Each pocket is a closed loop whose curves share end points
- [x] Arcs drawn directly with `sketchArcs.addByThreePoints`
- [x] Sketch named `Voronoi Pocket`
- [x] Parameters stored on the sketch as JSON (`sketch.attributes`, group `VoronoiPocket`) for a later "edit" command

## 5. Extrude cut  *(written, not yet run in Fusion)*

- [x] All sketch profiles collected into one cut
  - [ ] Compare the profile count with the number of pockets drawn and warn on a mismatch
- [x] `extrudes.createInput(profiles, CutFeatureOperation)` with a one-sided distance
- [x] Direction: compares the sketch's Z axis with the face's outward normal. Fusion accepts a cut that points away from the body and removes nothing, so the body volume is checked after the cut; if it did not go down, the cut is deleted and flipped.
- [x] `participantBodies` limited to the selected face's body
- [ ] Warn if depth ≥ local body thickness, or add a "Through all" option
- [x] Depth is a user parameter (`VoronoiDepth`, then `VoronoiDepth_2`, …) holding the expression as typed, and the cut's distance is that parameter's name. Not made for the preview, nor in a design without a timeline.
- [x] Sketch and cut folded into a timeline group named "Voronoi Pocket" (parametric designs only)

## 6. Preview, validation and errors  *(written, not yet run in Fusion)*

- [x] `inputChanged`: depth is hidden when "Cut pockets" is off
- [x] `validateInputs`: wall > 0, depth > 0, margin ≥ 0, radius ≥ 0, min width ≥ 0, all expressions valid
- [x] `executePreview`: builds the real result; Fusion rolls it back
  - [x] Outline preview: the pocket outlines are drawn as custom graphics lines on the face (`CustomGraphicsGroup`), with no sketch and no cut. Removed on OK, Cancel and Off.
  - [x] The face outline and the pattern are kept while the dialog is open; a change of depth alone reuses them
  - [x] The pattern is worked out in a frame on the face itself, not in a sketch, so the preview needs no sketch. For the real build its points are moved into the sketch's axes (also when the sketch is mirrored).
  - [ ] The outline preview does not show the depth (only the arrow does). A see-through pocket floor or a filled pocket area would.
- [x] `execute`: wrapped in try/except, readable error messages
- [x] Status line at the bottom of the dialog: pocket count, share of the face pocketed and time, or the error. In simple mode also the chosen cells, wall and radius.
- [x] Log file `voronoi_pocket.log` with every preview and run
- [x] Event handlers kept in module lists and cleared on `stop()`

## 7. Testing

- [x] Unit tests for `geometry.py` (run with plain Python or pytest):
  - [x] The same seed gives the same cells
  - [x] Cell areas add up to the face area when wall = 0
  - [x] Pockets keep the wall thickness between them and exactly the margin to the edge
  - [x] Shrinking a face: checked point by point against the true distance to the edge (L-shape, holes, curved outline, narrow neck, thin arm)
  - [x] Trimming a cell: checked point by point (inward corner, two pieces, hole inside, hole over the outline)
  - [x] Concave outline, curved outline with an inward waist, outline with holes
  - [x] Degenerate cases: 1 cell, 1 cell around a hole, duplicate points, tiny face, margin too big
- [x] `test_bridge.py`: edge stitching, closed sketch loops and the cut-direction logic, against a stand-in for the Fusion API. It cannot show that the real API behaves as expected.
- [ ] Manual test cases in Fusion:
  - [x] Rectangular plate (top face): sketch and cut work
  - [ ] Bottom face (normal pointing −Z) and a tilted face, to check sketch orientation and cut direction
  - [ ] Round plate (arc edges)
  - [x] L-shaped plate with a hole: sketch works (seen before the cut-direction fix)
  - [x] Curved plate with holes: sketch and cut work
  - [ ] The same curved plate again, to confirm the even border
  - [ ] Face on a body inside a sub-component
  - [ ] Document in inches vs mm
  - [ ] Parametric vs direct modelling mode
  - [ ] Undo: the whole operation should undo in one step
  - [ ] Stop and Run the add-in again: the button appears once, code edits are picked up
- [x] Timing of the maths alone, outside Fusion: 60 cells ≈ 0.1 s, 300 cells ≈ 0.2 s, 1000 cells ≈ 1.4 s; simple mode 0.5–0.7 s (it tries about 16 wall thicknesses over a few cell counts)
- [ ] Time sketch drawing and the cut inside Fusion at 200, 500 and 1000 cells; adjust `MAX_CELLS` in `config.py` if needed. Seen so far: 60 cells, sketch plus cut, 3 to 7 s depending on the part (the log now splits each run into maths, drawing and cutting).

## 8. Polish and packaging

- [x] Icons for every size (`tools/make_icons.py` redraws them)
- [ ] Tooltip with an illustration image (`toolClipFilename`)
- [x] README: how to install on Windows and macOS, what each input means
  - [ ] Screenshots
- [x] One zip for both systems (`tools/make_package.py`): no caches, log, saved settings or tests
- [ ] Install path notes:
  - Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`
  - macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`
- [ ] Choose a licence
- [ ] (Optional) Publish on the Autodesk App Store

## 9. Ideas for later

- [ ] Make OK itself faster. It is all Fusion's time: about 1.8 ms per sketch curve, plus the cut. Options: fewer curves (splines or arcs along curved edges instead of many short lines), or writing the outlines to a DXF file and importing that in one call.
- [ ] Rust (or any compiled) helper for the maths. Only worth it if changing a pattern value still feels slow: it would turn 0.5–0.7 s into a few hundredths, and nothing else. Costs: a build per system (Windows, Intel Mac, Apple silicon Mac), a helper program to start and stop, and two copies of the maths to keep the same.
- [ ] Select several faces at once
- [ ] Density gradient or attractor point (smaller cells near an edge or a chosen point)
- [ ] "Edit Voronoi" command: read the sketch attributes, regenerate, and replace the sketch and extrude (or look into the Custom Features API)
- [ ] Fillet or chamfer on pocket floor and top edges
- [ ] Through-cut mode (lightweight panels)
- [ ] Export the cell outlines to DXF for laser cutting
- [ ] Presets: "Light", "Dense", "CNC 6 mm end mill"

## 10. Stress-driven pattern (written; the Fusion side is not yet run in Fusion)

Status: `core/stress.py` works out the stress and turns it into a pattern,
with 15 tests, most against textbook answers (`py VoronoiPocket\tests\test_stress.py`).
Version 0.4.0 adds the Stress tab that uses it. The tab's own logic is tested
against a stand-in for Fusion; in Fusion itself it has not run yet.

A second tab in the dialog, "Stress", that switches on a pattern shaped by
load: where the part carries more force the walls are thicker, and where it
carries little they are as thin as allowed. The user says where the part is
held and where it is pushed or pulled (edges or holes of the face, with a
force and a direction). The add-in reads the body's physical material from
Fusion (stiffness, Poisson's ratio, yield strength), runs its own flat (2D)
stress calculation on the face outline, and gets a stress map of the face like
the usual coloured plots of a plate with a hole. That map then steers the
Voronoi pattern: more and smaller cells with thicker walls in the
high-stress areas, fewer and larger cells with the thinnest walls elsewhere.
The result is a design aid, not a proof of strength: it should be checked
with Fusion's own Simulation before a part is trusted.

- [x] Dialog: two tabs, "Pattern" and "Stress". The Stress tab has the on/off switch, pickers for held and loaded edges, force in newtons, force direction (an angle with a handle on the face), plate thickness, yield strength, safety factor, thinnest wall, size ratio and a switch for the stress colours. With stress on, the Pattern tab keeps Cells, Corner radius, Min pocket width, Border margin and Seed.
  - [x] Forces at points (0.5.0): up to 6 load points (corners of the face, sketch points, construction points), each with its own force and its own direction handle at the point. They add to the edge force, or replace it when no loaded edge is picked.
    - [ ] A point in the middle of the face needs a sketch point drawn there first. Fusion only lets a dialog pick existing things, and one face can be picked only once, so "click anywhere on the face" would need its own mouse handling.
    - [ ] The rows go by picking order. Unpicking point 2 of 3 moves point 3's force and direction up into row 2's values.
    - [ ] The per-point values are not remembered between sessions, only the starting force.
    - [ ] A point force is spread over the mesh points within 1.5 triangle sizes, so the stress right at the point depends on the mesh. A "pad size" setting would make that the user's choice.
    - [ ] Load points and the face must be in the same component; a point picked from another component of an assembly lands in the wrong place.
  - [ ] Still one edge force only, spread evenly along the loaded edges. A bolt or pin really presses on one side of its hole. Several edge forces in different directions are not possible yet.
  - [ ] Held points (only edges can be held)
- [x] Safety factor (0.5.0): the status line shows the lowest safety factor of the solid plate (yield strength divided by the highest stress) next to the target, and the face can be coloured by safety factor: red at 1 or below, yellow about 2, green about 4, blue 8 and above.
  - [x] 0.6.0: the safety factor of the plate with pockets is shown next to it, from the check of section 11.1 (a quick method with known limits, not a fine mesh of the pocketed shape).
- [x] Material: yield strength and Poisson's ratio are looked up by name in the body's physical material. Shown in the dialog and editable there; when it cannot be read the dialog says so and the typed value is used.
  - [x] Seen in Fusion: Poisson's ratio is read (0.3 for Steel). The yield strength is there as `structural_minimum_yield_stress`, in units named in words ("Kilopascal").
  - [ ] Confirm in Fusion that 0.5.0 now fills in 207 MPa for Steel, and try one other material
- [x] Plate thickness: taken from the size of the body's bounding box along the face's normal, shown in the dialog and editable. Right for a plate lying square to the axes, too big for a turned one or for a face on a thicker part of the body.
  - [ ] Measure the thickness under the face itself (a ray through the body)
- [x] The stress is worked out once and kept until the held edges, loaded edges, force, direction or thickness change. Changing depth, cells or the material only redoes the pattern.
- [x] Mesh (`make_mesh`): the face is cut into equal-sided triangles; points near the edge are moved onto it, so the mesh follows the outline and holes exactly (no area lost), and sharp corners are kept
  - [ ] The triangles are the same size everywhere. Smaller ones near holes and corners would sharpen the peaks (see accuracy below).
- [x] Solver (`solve_plate`): flat-plate (plane stress) calculation. Supports along edges or whole hole rims (both directions or one), forces spread along edges or rims, any material stiffness and thickness. Gives the stress at every point and a "how loaded" number (von Mises) to compare with the yield strength.
- [x] Tests against known answers: even pull and pure shear (exact), a bent plate against beam theory (within 6%), a pulled plate with a hole against the textbook formula, a force on a hole rim
- [x] Accuracy, measured: a border margin away from a hole, where pockets go, within about 5% of theory. Right on a hole's rim the peak reads low (2.4 times the far stress against 3.1 in theory, default mesh). Good for steering walls, not for judging strength.
- [x] Speed in plain Python, measured: 800 points 0.5 s, 1,500 points 1.4 s, 2,400 points 2.5 s (the default), 3,600 points 5 s, 5,800 points 12 s
  - [ ] **Rust decision.** The default is usable as it is, since the stress is worked out once per change of load, not per preview. A much finer mesh (for sharper peaks) is out of reach in plain Python, so this is the one place a compiled helper would buy something. Decide once the default has been tried on real parts.
- [x] Pattern (`build_stress_pattern`): every cell gets its own wall thickness from the highest stress in it. The rule is "keep material in proportion to the load": at stress equal to the allowed stress (yield strength divided by a safety factor) the cell stays solid; at low stress the wall is the thinnest allowed. Border margin and corner rounding work as before.
  - [x] Cell size follows the load too (`size_ratio`, default 2.5): the least loaded areas of the part get the widest cells, the most loaded ones cells that many times narrower. Seen on the test plates: cells from about 7 mm wide where loaded to 22–28 mm where idle, with the same number of cells in total.
    - [ ] Cell size goes by the load compared with the rest of the part, wall thickness by the load compared with the material's limit. Decide in the dialog whether the size ratio is a user setting (probably yes, with 1 meaning "even cells").
    - [ ] Cells inside an area that stays solid are small and make no pockets, so they are spent for nothing. Fine for now; raise the cell count if a part has large solid areas.
  - [ ] The rule assumes the ribs carry load as well as solid material. Ribs that run across the load carry less, and a floor left under blind pockets carries some. Refine the rule or cover it with the safety factor.
- [x] Preview (Outlines mode): held edges in green, loaded edges in red, a red arrow for the force, and the face coloured by stress from blue (idle) to red (at the allowed stress). The picture also stays up when no pocket fits, to show why.
  - [ ] No colour key in the dialog; the status line gives the highest and the allowed stress in MPa
- [x] Units: lengths in cm, force in N, so stress in N/cm² inside; shown as MPa (1 MPa = 100 N/cm²)
- [ ] Limits to state in the dialog and README:
  - the check of the pocketed plate is a quick method: it reads the walls within about 30% where they are wide next to the mesh, misses the peaks in pocket corners, and its correction for thin walls was only measured for cuts right through (section 11.1)
  - it is flat: forces out of the face's plane, bending, and the floor left under blind pockets are not counted

## 11. Closed loop: check the pocketed plate and correct the pattern (11.1 written, 11.2 and 11.3 open)

Before 0.6.0 the stress was worked out once, on the solid plate, and the
pattern was made from that. Cutting the pockets changes the stress: the load
that the removed material carried moves into the walls. This section closes
the loop:
make the pattern, work out the stress of the pocketed plate, check every
pocket against the allowed stress, correct the pattern, and repeat a set
number of times. Order of work: 11.1 first (the loop), then 11.2 (cell
size), then 11.3 (wall thickness). 11.2 and 11.3 mostly exist already and
get their real value from the loop.

### 11.1 The loop (written in 0.6.0, not yet run in Fusion)

In `core/stress.py`: `solve_pocketed` (stress with the pockets cut),
`refine_pattern` (the loop), `thin_wall_factor` (a correction, see below).
The Stress tab has **Iterations** (0 to 10, default 3) and **Check in
preview**.

- [x] **How the pocketed plate is calculated: "softer triangles".** The same mesh as for the solid plate; every triangle is made softer by the part of its material the pockets take away, worked out from the exact area a pocket covers of it. The stress read is that of the material left.
  - [x] The pocket depth counts: with pockets 60% deep, a floor of 40% stays and carries load. If no cut is made (sketch only), the pockets are taken to go right through.
  - [x] The inside of a pocket cut right through is taken out of the calculation, except one soft row of triangles around the material.
  - [ ] Walls as beams (a few hundred points, a stress for every single wall): not done
- [x] **Measured against a fine mesh of the real pocketed shape** (a pulled 100 x 60 mm plate, 30 pockets cut through, walls 3 mm and 2 mm, four triangles across every wall in the fine mesh). "Nearly highest" below is the level 95% of a cell's wall material stays under.
  - [x] What the quick method reads as the highest wall stress of a cell, as a part of the fine mesh's nearly highest value, by how wide the walls are in triangles of the quick mesh:

    | Wall width in triangles | 2.3 | 1.7 | 1.2 | 1.1 | 0.86 | 0.80 | 0.60 | 0.57 |
    | --- | --- | --- | --- | --- | --- | --- | --- | --- |
    | Quick / fine (middle cell) | 1.26 | 1.04–1.11 | 0.84 | 0.81 | 0.69 | 0.62 | 0.53 | 0.47 |

  - [x] So walls thinner than about 1.6 triangles read low, evenly. `thin_wall_factor` undoes it with the line fitted to this table (reading = 0.25 + 0.47 x width), held at a factor of 2 below 0.57 triangles, where nothing was measured.
  - [x] Single cells scatter around that: from about 0.6 to 1.4 times the middle value.
  - [x] The sharp peaks in rounded pocket corners are not seen: the fine mesh's single highest value is about 1.5 times its nearly highest one.
  - [x] The plate as a whole comes out too stiff: by 9% with walls 2.3 triangles wide, 21% at 1.7, 40% at 1.1, 100% at 0.6.
  - [ ] All of this was measured for cuts right through, on one plate with an even pull. With a floor under the pockets less should be missed, but that is not measured (it needs a 3D calculation): the correction is taken in proportion to the depth, as a cautious guess. Compare one real part with Fusion's Simulation.
  - [ ] Typical case in the add-in: the mesh is 1/65 of the face's diagonal, so on a 200 mm plate a triangle is about 3.4 mm and a 1.5 mm wall is 0.44 triangles wide, below what was measured. A finer mesh for the check would fix that, at the cost of time (the compiled helper again).
- [x] **A finding worth knowing:** cut right through, the pattern made from the solid plate's stress alone does not hold. On the three test plates 58 to 60 of 60 cells were over the limit at the first check, with wall stress 6 to 11 times the allowed. Thin walls between through pockets bend, and carry far less than their share of material suggests. With a floor of 40% left, 0 to 2 of 60 were over.
- [x] Validity check per cell: the highest stress at the mesh points inside the border band that are nearest to the cell's centre (a point in a wall counts for both cells beside it), against the allowed stress. Cells with no pocket are not counted as "over": they are solid already.
- [x] Correction: material is kept in proportion to the load found. Over the aim (95% of the allowed stress): more material, by the load ratio to the power 0.7. Under 85% of the aim: less, by the power 0.4. In between: left alone. Never thinner than the thinnest wall.
  - [x] Against swinging between too thick and too thin: each cell remembers the most material at which it was still over the limit and the least at which it was fine, and a correction that would step past one of these goes halfway instead.
  - [x] The cells stay where they are during the loop; only the walls change. Moving the cells too is 11.2.
- [x] **Iterations** in the Stress tab: how many corrections at most. 0 = the pattern as before, no check. After the last correction the result is checked once more, so N iterations are at most N + 1 stress calculations.
  - [x] Stops early when no pocket is over the limit and no wall changed by more than 1% of its cell's size.
  - [x] Returns the best pattern that was checked (pockets before none, then fewest cells over the limit, then most pocket area), so more rounds are never worse.
  - [x] When rounds run out with cells over the limit: the status line says how many, their pockets are outlined in red in the preview, and after OK a message says so with the stress and the allowed stress (the status line is gone by then).
- [x] Status line: lowest safety factor of the solid plate and of the plate with pockets, rounds used, time taken.
- [x] Colours (Stress and Safety factor) show the plate with pockets once it has been checked, the solid plate before.
- [x] **When the loop runs.** On OK, always. In the preview only with **Check in preview** ticked (default off), because every change would then take several seconds. A checked result is kept until the loads, the pattern values, the iterations or the depth change; until it has been checked again the preview shows the unchecked pattern and says so.
- [x] Time, measured (2,000 points, 60 cells): 2–3 s per check with a floor, 3–4 s cut right through (about twice the solver steps of the solid plate). With a floor the loop settled after 1 to 2 corrections (4–8 s); cut through it took 4 to 7 corrections to get all, or all but one, of the cells under the limit (24–33 s).
  - [x] Starting from the previous round's answer: built in, saves only 10–15% of the steps.
  - [x] Solving to 1 part in 100,000 instead of 10 million: same stresses, about 10% fewer steps.
  - [ ] A better starting pattern for through cuts would save most rounds: the first pass could use the bending rule instead of "material in proportion to load".
  - [ ] The check is the place where a compiled helper (section 9) would pay most: it runs the solver 2 to 8 times.
- [x] Tests (8 new in `test_stress.py`, 2 in `test_bridge.py`, more steps in `test_dialog.py`):
  - [x] A plate softened evenly by half stretches exactly twice as far and carries exactly twice the stress
  - [x] Material taken away by the triangles adds up to the pockets' area (within 0.5%)
  - [x] One 40 x 40 mm pocket cut through a pulled plate: 3 times the stress beside it (a third of the width is left), stretch within 10% of the same plate meshed with a real hole
  - [x] Plate with a hole, cut through: 40 of 40 cells over the limit at first, 19, 4, 2, 1, 1, then 0 after six corrections
  - [x] More rounds are never worse; Iterations = 0 gives exactly the old pattern; the same input gives the same result
  - [x] With a floor, less is over the limit and more can be pocketed
  - [x] Slow test against the fine mesh, to keep the error on record: `py VoronoiPocket\tests\test_stress.py --slow` (about a minute)
  - [ ] Even pull ending with the same wall everywhere: not a test. The loop ends with walls from 4 to 14 mm there, since a Voronoi pattern's cells differ.
- [ ] To see in Fusion: Iterations and Check in preview on the Stress tab, the status lines, red outlines, the message after OK, and how long OK takes with 3 iterations on a real part

### 11.2 Smaller, denser pockets where the stress is high

Exists in a first form (`size_ratio`, 0.4.0): cells are narrower where the
solid plate is loaded. To do:

- [ ] Drive the density from the stress of the pocketed plate (from the loop), not only from the solid plate
- [ ] Put the size ratio in the dialog: 1 = even cells, 2.5 = default, with an upper limit that the cell count can support
- [ ] Smooth the density map before placing cells, so one hot triangle does not make one tiny cell among large ones
- [ ] Limit how fast the size may change from one cell to its neighbour (for example no more than 1.5 times), for an even-looking transition
- [ ] Cells that fall in areas that stay solid are wasted: move them out to where pockets are made, or take them out of the count
- [ ] Optional "automatic cell count": work out the count from the face area and the smallest and largest cell size wanted, instead of asking for a number
- [ ] Smallest cell limit from the tool: a cell must not shrink below what the corner radius and min pocket width can still cut
- [ ] Later: stretch cells along the direction of the force, so walls line up with the load path
- [ ] Tests: cell size falls steadily as stress rises; the neighbour limit is kept; the total pocketing share is reported

### 11.3 Variable wall thickness, never below the defined thickness

Exists in a first form: each cell has its own wall thickness from the load,
and the Stress tab has its own "thinnest wall". To do:

- [ ] One rule for the lower limit: the "Wall thickness" of the Pattern tab is the defined thickness, and no wall anywhere may be thinner. Remove the separate "thinnest wall" value or tie it to this one.
- [ ] A wall between two pockets is made half by each cell, so its thickness is the average of the two. Check that this average, and not only each half, is at or above the defined thickness, including where a small cell sits next to a large one.
- [ ] Thickness per wall instead of per cell (needs Option B, or the stress looked up along each wall): a pocket can then have one thick side towards the load and thin sides elsewhere
- [ ] Later: walls that taper along their length, thicker at the loaded end
- [ ] Upper limit: a wall wider than the cell allows turns the cell solid; decide whether such a cell should be merged with a neighbour instead
- [ ] Corner rounding and the min pocket width only ever take pocket area away, so they cannot thin a wall. Keep a test that proves it.
- [ ] Test on every stress pattern: measure the real distance between each pair of neighbouring pockets and between pockets and the border, and fail if any is under the defined thickness or the border margin
- [ ] Status line: thinnest and thickest wall actually made

---

## Gotchas to remember

- **The Fusion API works in centimetres internally.** `ValueCommandInput.value` returns cm. Convert with `design.unitsManager`.
- `sketches.add(face)` projects the face edges automatically; `addWithoutEdges(face)` doesn't.
- Without `isComputeDeferred`, sketches with many curves are very slow.
- Keep references to event handlers in a global list, or Python garbage collection kills them.
- When a face is selected in an assembly you get a proxy object. Use `assemblyContext` and `nativeObject` to work with it.
- Preview runs on every input change. Keep it fast or make it optional.
- Fusion keeps Python modules loaded between Stop and Run. `VoronoiPocket.py` reloads them in `run()` so edits take effect without a restart.
