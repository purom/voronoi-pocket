# Voronoi Pocket

A Fusion add-in. Pick a flat face and it draws a Voronoi cell pattern on it as
a sketch, then cuts the cells into the body as pockets.

The same folder works on Windows and on macOS. It uses only what ships with
Fusion (no extra Python packages).

## Install

1. Put the `VoronoiPocket` folder anywhere on your computer. Keep the folder
   name: Fusion needs the folder, `VoronoiPocket.py` and
   `VoronoiPocket.manifest` to share one name.
2. In Fusion press **Shift+S** (Utilities > Add-Ins > Scripts and Add-Ins).
3. Click **+**, choose **Script or add-in from device**, and pick the
   `VoronoiPocket` folder.
4. Switch **Run** on for VoronoiPocket. Tick **Run on Startup** if you want it
   loaded every time.

Fusion also lists the add-in by itself, after a restart, if the folder is in
its own add-ins folder:

- Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`
- macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`

On macOS the Library folder is hidden. In Finder choose Go > Go to Folder and
paste the path.

## Use

Solid tab > Create > **Voronoi Pocket**. Pick a flat face, set the values,
press OK. The dialog has two tabs: **Pattern** for the pockets themselves and
**Stress** for shaping them by load.

### Simple mode

| Input | Meaning |
| --- | --- |
| Border margin | Solid band kept along the face edge and around holes. |
| Pocketing % | How much of the face becomes pocket. 80 means the material left on the face is 20% of its area. |
| Seed | Change it for a different pattern. The same seed always gives the same pattern. |

The add-in works out the rest. It picks the wall thickness that gives the
asked percentage, and the number of cells for which those walls are about as
wide as the border margin. The status line at the bottom of the dialog shows
what it chose. If the percentage does not fit (the border band alone takes too
much), you get the most that fits and the status line says so.

### All settings (simple mode off)

| Input | Meaning |
| --- | --- |
| Border margin | As above. |
| Cells | How many Voronoi cells to place. |
| Smoothing | 0 gives raw random cells; higher values make them more even. |
| Wall thickness | Width of the rib between two pockets. |
| Corner radius | Radius of every pocket corner. For milling, use at least the tool radius. |
| Min pocket width | Pockets narrower than this are left out. |
| Seed | As above. |

### Shaped by stress (the Stress tab)

Switch on **Shape by stress** and the add-in works out how the plate is
loaded, then makes the pockets small with thick walls where it works hard and
big with thin walls where it is idle.

| Input | Meaning |
| --- | --- |
| Held edges | Edges of the face that cannot move: where the part is bolted, clamped or welded. A hole rim counts as an edge. |
| Loaded edges | Edges of the face the edge force pushes or pulls on. With several, the force is shared by their length. Leave empty to use only forces at points. |
| Edge force (N) | The total force on the loaded edges, in newtons. 1 kg of weight is about 10 N. |
| Edge force direction | Which way that force points, turned within the face. Type an angle or turn the handle on the model. |
| Load points | Up to 6 points on the face that carry a force of their own: corners of the face, sketch points or construction points. |
| Point 1 force (N), Point 1 direction, ... | One pair for every load point picked, in the order they were picked. The direction has its own handle at the point. A force of 0 switches a point off. |
| Plate thickness | Filled in from the body when a face is picked. Correct it if it is wrong. |
| Yield strength (MPa) | Filled in from the body's material. Check it against the material's data. |
| Target safety factor | The safety factor the pattern is sized for: the material may carry its yield strength divided by this. |
| Thinnest wall | The wall used where the plate is hardly loaded. |
| Size ratio | How many times wider the pockets in idle areas are than in the most loaded areas. 1 gives pockets of about one size. |
| Iterations | How many times the result is checked with the pockets cut and corrected (0 to 10). 0 switches the check off. See below. |
| Check in preview | Off: the preview shows the pattern before the check and OK runs the check. On: the preview runs it too, so every change takes several seconds. |
| Colours | What the face is coloured by in the Outlines preview: Stress, Safety factor, or Off. |

To put a force somewhere in the middle of the face, draw a sketch with a point
there first, then pick that point as a load point.

In the preview the held edges are green, the loaded edges red, and every force
has a red arrow (a bigger force gets a longer one). The face colours mean:

- **Stress**: blue is idle, red is at the allowed stress (yield strength
  divided by the target safety factor).
- **Safety factor** (yield strength divided by stress): red is 1 or below,
  yellow about 2, green about 4, blue 8 and above.

The status line gives the highest stress, the allowed stress, and the lowest
safety factor of the solid plate next to your target.

#### The check with pockets (Iterations)

Cutting pockets changes the stress: what the removed material carried moves
into the walls. With **Iterations** above 0 the add-in works out the stress
again with the pockets cut, thickens the walls of pockets that are over the
allowed stress, thins those far under it (never below **Thinnest wall**), and
repeats, up to the number you set. It stops early once nothing is over the
limit.

- The pocket depth counts. A floor left under the pockets carries load, so
  shallow pockets can have thinner walls than pockets cut right through. With
  **Cut pockets** off the depth is not known and a cut right through is assumed.
- It runs when you press OK. Each round takes a few seconds, so the preview
  only runs it with **Check in preview** ticked; otherwise the status line
  says the pattern shown is not checked yet.
- After the check the status line shows the lowest safety factor with
  pockets, and the colours show the plate with its pockets.
- Pockets still over the limit when the rounds run out are outlined in red,
  and after OK a message tells you. Then try more iterations, a thicker
  thinnest wall, shallower pockets or fewer cells.

Cells, corner radius, minimum pocket width, border margin and seed come from
the Pattern tab.

**This is a design aid, not a strength check.** Keep in mind:

- The check with pockets is a quick estimate on the same coarse mesh. Against
  a fine calculation of a plate cut right through it read the walls within
  about 30% when they were at least 1.6 mesh triangles wide; thinner walls
  read low and are corrected by a measured factor. It does not see the sharp
  peaks in pocket corners (about 1.5 times higher).
- It is flat: only forces within the face's plane count. Bending out of the
  plane is not part of it. A floor left under the pockets is counted as a
  thinner plate, not as the real 3D shape.
- With Iterations at 0, stress and safety factor are those of the solid plate
  only, and a pattern cut right through will be much weaker than they suggest:
  thin walls between through pockets bend.
- Peaks right at a hole's rim or a sharp inside corner read low (about 2.4
  times the far stress where theory says about 3).
- A force at a point is spread over a small patch around it (about 1/40 of the
  face's size), as a bolt head or a pad would. The stress right at the point
  depends on that patch, so read it as "high here", not as a number.
- The wall rule assumes the ribs carry load as well as solid material. Ribs
  that run across the load carry less.

Check the finished part with Fusion's own Simulation before relying on it.

### Depth

With **Cut pockets** on, the depth appears once a face is picked: type a value
or drag the arrow on the model. Turn Cut pockets off to get only the sketch.

The depth is saved as a user parameter named `VoronoiDepth` (a second pattern
in the same design gets `VoronoiDepth_2`, and so on). Change it later in
Modify > Change Parameters and the pockets follow.

### Preview

The **Preview** drop-down has three choices:

- **Outlines (fast)**: the pocket shapes are drawn on the face straight away,
  as plain lines. Nothing is added to the design until you press OK, so
  changing values and dragging the depth arrow stay quick. OK then makes the
  sketch and the cut once, which takes a few seconds.
- **Real cut (slow)**: every change rebuilds the real sketch and cut. Use it
  when you want to see the actual pockets before pressing OK.
- **Off**: nothing is shown until OK.

## If something goes wrong

Every preview and run is written to `voronoi_pocket.log` in this folder, with
the full error text. The last dialog values are kept in `settings.json`; delete
it to go back to the defaults.

After changing the add-in's code, switch Run off and on again (Shift+S).
