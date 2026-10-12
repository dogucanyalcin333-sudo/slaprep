*[Türkçe](README.tr.md)*

# SLAPrep — automatic print preparation for split resin figures

SLAPrep takes a split figure kit (a folder of STL parts) and gets it ready for CHITUBOX: it solves how the
parts fit together, scales the kit, hollows the parts, drills drain holes **only into mating faces**, orients
every part and nests them onto build plates.

Mating faces are the surfaces that end up covered by the neighbouring part once the figure is assembled, so the
drain holes disappear when you glue the kit together — no plugging or filling after the print. All that is left
to do in CHITUBOX is adding supports and slicing.

The bottom of the base — and the back face in wall-mount mode — is also hidden after assembly, but it is still
an outer surface: a hole there stays open. That is why no holes are drilled there by default. You can turn them
on in the *Hollow / holes* card (`--holes-on-bottom`, `--holes-on-wall-back`).

Written for Windows + CHITUBOX; needs Python 3.11 or newer.

> **Language note:** the application's interface, reports and command-line messages are currently **Turkish
> only**. An English interface is in progress. This README describes every feature in English, and the CLI flags
> and output file names are already English.

## Installation

Download the repository (**Code → Download ZIP** on GitHub, or `git clone`), then double-click `install.bat`.
It creates a virtual environment, installs the package and puts an **SLAPrep** shortcut on your desktop.
Run `install.bat` again if you move the folder.

```
git clone https://github.com/dogucanyalcin333-sudo/slaprep.git
cd slaprep
install.bat
```

## Using the window

The window has four regions: the **kit bar** at the top (folder, Browse, Report, Folders), **setting cards** on
the left, **tabs** on the right (Parts, Plates, Summary, Assembly check, Queue) and the **action bar** at the
bottom (Analyse, Prepare, Stop, Add to queue, Low priority, Log). Hovering the ⓘ next to a setting shows what it
does. SLAPrep runs as a single window: dropping a folder on the shortcut while it is open loads the kit in the
window you already have instead of opening a second one.

1. Open the **SLAPrep** shortcut. Drag the kit folder onto the window or onto the shortcut (or use **Browse…**).
2. **Analyse**: finds the assembly, the height, alternative parts (V1/V2, merged/split) and the hidden surfaces.
3. Pick your settings in the cards on the left (clicking a card's title folds it; the ones you leave open are
   remembered):
   - **Operations**: two checkboxes — **Hollow + holes** (parts are hollowed, holes go into mating faces only)
     and **Orient + nest on plates**. Both on = full preparation. With only nesting on, parts stay solid; with
     only hollowing on, nothing is nested and each part is written out as its own STL in its original pose. The
     card of a disabled operation folds itself away, and the main button is renamed accordingly (Prepare / Nest
     on plates / Hollow + holes).
   - **Size**: Original / Target height / Scale. For a target height, choose "including base" or "figure only".
     The height that will actually be printed is shown underneath. If no assembly could be found, enter the
     original height from the product page.
   - **Hollow and holes**: wall thickness, hole diameter (Automatic: 3 mm up to the largest diameter as the
     cavity grows; or Fixed) and the minimum number of holes per cavity. *Extra hole locations* (base bottom,
     wall back, visible surface) drill into outer surfaces and have to be plugged after the print, so they are
     off by default.
   - **Assembly**: the *wall-mounted* switch (back faces become usable for holes), the up axis, the **front
     direction** (supports are steered to mating faces first, then to the figure's back and underside; if the
     "Front" view in Assembly check shows the figure's back, correct it here), subfolders (an unchecked folder
     is not prepared) and alternative parts (listed after the analysis; patterns work too, e.g. `*_Key`), plus
     "also print unselected alternatives" and "also print scale-reference / advertisement parts".
   - **Printer and plates**: optimise for **shortest time** (default) or **fewest plates**. On a resin printer a
     plate takes as long as its tallest part, so "shortest time" groups the tall parts onto the same plate and
     fills the space around them with short ones. Also: spacing between parts, splitting by folder (Auto/Yes/No)
     and your CHITUBOX layer settings, used for the time estimate.
   - **Resin**: price per litre, optionally a second price for the "separate plate" resin (clear, for example),
     bottle size and the support + waste margin (15%). Prices are stored on this computer, not in the kit, and
     changing them updates the results without re-running the preparation.
4. The **Parts** tab:
   - The box at the start of a row **includes or excludes** the part. The box in the **Separate plate** column
     nests that part onto plates of its own (`plates\ayrı\plate_01.stl`), which is what you want for a different
     resin. Clicking the **Fill** cell cycles *Automatic / Solid / Hollow* (Solid: printed solid even if the tool
     could hollow it — clear resin, for instance; Hollow: hollowed even if it is below the volume threshold).
   - Select several parts (Ctrl/Shift) and the **selection bar** above the table changes fill, separate-plate and
     include for all of them at once; a mixed state is shown with an intermediate mark. The right-click menu does
     the same things.
   - The **Find part** box filters the list. After an analysis or preparation the volume, result (Hollow / Solid /
     Not printed), holes, prepared resin volume and plate columns fill in. Your choices are saved into the kit
     when you prepare it.
5. The **Assembly check** tab: front and side views of the assembly (**Front / Side / Height / Photo**) and how
   each part was placed. Green = reliable, orange = check it, red = not placed. The number in the tab title is
   how many parts need checking. If a part sits in the wrong place, select it in the list and either:
   - **Detach from assembly** (it is left out of the assembly and the height, but still printed), or
   - pick its correct neighbour (the anchor) from the dropdown and press **Join** (parts joined to the same
     neighbour — two hands, say — are placed together without colliding); if it lands wrong, use **Try another
     position**,
   - then press **Re-solve assembly**. Your corrections are saved into the kit.
   - If the base and the figure were exported in separate coordinate systems, the figure ends up standing next to
     the base: select the figure's foot part, choose the base part as its anchor and press **Join** — the whole
     figure then sits on top of the base.
   - **Photo**: grey renders of the assembly from 8 angles, side by side with a studio photo; use the angle and
     photo arrows and **Add photo…** (or drop a photo on the window; a copy is kept under `_slaprep\photos`).
     Renders that came with the kit are listed automatically. **Height**: a true-proportion view with a live
     ruler that follows the Size setting.
   - Scale-reference parts (e.g. `HEIGHT_PILLAR`) and the **advertisement / watermark STLs** that download sites
     add are recognised from their geometry: they are not printed, not counted in the height, and listed in the
     report (to print them anyway, use the checkbox in the Assembly card or `--print-reference`).
   - A 0.4–1.2 mm assembly gap between parts still counts as contact. A part that is not watertight is repaired
     automatically (tiny holes and "flap" triangles are closed losslessly); if the repair would distort the
     shape, it is skipped and the part is printed **solid** in its original form, with the reason in the report.
6. **Prepare** (named "Nest on plates" or "Hollow + holes" depending on the operations you enabled). When it
   finishes:
   - **Summary**: cards for the parts to print, the height, plates · time and resin, with the assembly details
     and any warnings underneath.
   - **Plates**: cards for the plate count, total time, resin and the optimisation target; then per plate the
     parts, the tallest part, the time and the resin (ml · cost). Double-clicking a plate opens its STL in your
     default application (CHITUBOX). **Measured time**: after printing a plate, enter the time the printer
     actually reported (e.g. `3:31`) and the estimate is calibrated to it (one measurement sets the layer time;
     a few measurements at different heights also set the per-plate overhead). Measurements are stored on this
     computer and apply to every kit, including on the command line.
   - The **Support** column in the **Parts** table: how much of the area that will need supports falls on mating
     faces, on the back or underneath. Orientation prefers supports on mating faces first, then on the back and
     the underside, keeping the front and the sides clean (the report gives "support: hidden % · back/bottom % ·
     front/side %" for every part).
   - **Report** at the top opens the illustrated report; **Folders** opens the plate / part files and the output
     folder.
7. The **Low priority** switch in the bottom bar runs the job at low CPU priority, so the computer stays usable.
   **Log** opens the run output (it opens by itself if something fails).
8. **Overnight queue** (Queue tab): open each kit, set it up, press **Add to queue** at the bottom, then press
   **Prepare all** in the Queue tab. The kits are prepared one after another with the settings they had when you
   queued them; in the morning every kit's result (plates, time, resin, problems) is in one list, and
   double-clicking a row opens that kit. The queue survives closing the application; a kit that was interrupted
   is prepared from the start next time. You can also press **Prepare all** while a single kit is being
   prepared: the queue starts when that job finishes, and a job that just finished is not prepared a second time
   if it is waiting in the queue with the same settings.

## Quick mode

Drop a kit folder onto `slaprep.bat` and it is prepared with the saved (or default) settings.

## Output (`<kit>\_slaprep\`)

| File | Contents |
|---|---|
| `plates\plate_01.stl` … | The files to open in CHITUBOX. With several subfolders in the kit: `plates\<folder>\plate_01.stl` |
| `parts\` | One STL per part (oriented, hollowed, drilled) |
| `report.html` | Illustrated report |
| `prep.json`, `analysis.json` | Machine-readable results |
| `slaprep.yaml` | The last settings for this kit (the defaults on the next run) |

Your original STL files are never touched.

## Command line

```
slaprep analyze "<folder>"
slaprep prep "<folder>" --height 150 [--height-mode figure] [--wall 2] [--hole 3 | --hole-max 5] [--holes 4]
                       [--variant "*_Key"] [--exclude Base,lab] [--wall-mount] [--up-axis y] [--only Head,Hat]
slaprep scan "<root folder>"     # classifies every kit underneath, reading bounding boxes only
slaprep list                     # recently run kits

# assembly corrections (for analyze and prep; saved into the kit)
slaprep analyze "<folder>" --detach Arm_Right,Sword_1        # keep wrongly placed parts out of the assembly
slaprep analyze "<folder>" --join Head_v2:Miquella_Torso     # ANCHOR:PART — join the part to the anchor
slaprep analyze "<folder>" --join Torso:Hand_L:2             # ...using candidate position 2
slaprep analyze "<folder>" --clear-fixes                     # delete the saved corrections
```

## Troubleshooting

- **"Out of memory" / Windows "the paging file is too small"**: this is RAM + virtual memory, not disk space.
  In big kits (100–250 MB of STL per part) loading a single part can need 5–6 GB; SLAPrep adapts how many parts
  it loads in parallel to the free memory, but if it still runs out, close other programs (CHITUBOX, browser)
  and try again. The drive holding Windows' page file needs free space (System → Advanced → Performance →
  Virtual memory).
- An interrupted run does no harm: cache files are never written half-way, and the next analysis carries on
  where it stopped, skipping the parts it already loaded.
- **"Parts that do not fit the printer"**: the part is left off the plates but still written to `parts\` as its
  own file. The warning gives the scale that would fit (a `--scale` or `--height` suggestion); either shrink the
  kit that far or cut the part up in CHITUBOX.
- **The figure lies on its side in the preview**: the up axis is derived from the direction in which the assembly
  can stand without tipping over; if it is still wrong, fix it with `--up-axis y` (or `z`, `-y`, …).
- **A set of parts you do not want** (a second base with names in another language, for instance): `--exclude`
  also accepts folder names, including nested ones (`--exclude OUISTICRAM`).
- **The kit contains several versions of the same figure** (split / single-piece / bust; names like Split, Merge,
  BUST): the version with the most parts is prepared; select another one with e.g. `--variant "*BUST*"`.
- **"Subfolders are in separate coordinate systems"**: the base and the figure were exported separately. Each one
  is assembled correctly in itself and they stand side by side in the preview, but how the figure sits on the
  base cannot be known from the files. To seat it, **Join** the figure's foot part to the base part in Assembly
  check (CLI: `--join BASE:FOOT`).
- **"A lower orientation was chosen for print time"**: the warning gives the height and the largest layer
  cross-section (before → after). An alternative orientation may not leave more undrained suction cups than the
  best orientation, and may increase the layer cross-section by at most 1.5× (a large cross-section means a
  large peel force off the FEP).
- **"Parts sitting at the origin were seated"**: parts left at the wrong position in the file were placed using
  their mating surfaces; they show up orange in the Assembly check tab, so check them in the preview.

## Development: tests and the regression set

```
.venv\Scripts\python -m pytest tests -m "not rango"     # fast unit tests (~1 min, no kit needed)
.venv\Scripts\python tools\regress.py --quick           # do the known kits still assemble as expected
.venv\Scripts\python tools\regress.py                   # everything (long; includes the auto-assembly cases)
```

The regression set points at kit folders on your own computer, so it is not in the repository: the format is in
`tools\regress_kits.example.yaml` — copy it to `tools\regress_kits.yaml` and fill in your own kits. The Rango
test reads its kit paths from the `SLAPREP_RANGO` / `SLAPREP_RANGO_ORIG` environment variables (the test is
skipped if they are not set).

Run the regression after every change to the assembly, variant or auto-assembly code: a kit that used to be
solved correctly shows up here if it breaks. When you have verified a new kit (preview + real height), add it
with its expected values to `tools\regress_kits.yaml` and write down in the `verified` field how you checked it.

`ARGE.md` documents the R&D behind plate nesting and hole placement, with the measurements behind each decision
(in Turkish).

## License

MIT — see `LICENSE`.
