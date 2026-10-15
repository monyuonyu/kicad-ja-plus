**English** | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

> Translated from [README.ja.md](README.ja.md) (the Japanese version is the original) at commit `fab6b11`.

# kicad-ja-plus — an enhanced KiCad that can work on boards without a GUI

This is KiCad with the additions listed below. It comes in two versions.

| Version | Best for |
|---|---|
| **10.0.6** (recommended) | Based on the latest stable release. Also includes fixes for fabrication output and DRC bugs taken from upstream MRs. **Runs as-is on headless servers, in CI, and from AI agents** |
| 8.0.9 | When you want to keep using your edited files with KiCad 8 |

What was added:

- Tools for working on boards and schematics without opening the GUI (routing, moving footprints, DRC/ERC, 3D images, comparing fabrication output, circuit simulation)
- Features and fixes from KiCad 9–11 and from upstream MRs

**The file format is the same as upstream KiCad of the same version**, so files you edit open as-is in regular KiCad (10 or 8).

## What it can do

There is a single entry point, `kicad-local` (`kicad-local help` lists the commands).

```
kicad-local drc 基板.kicad_pcb            # DRC summary (count and location per violation type)
kicad-local erc 回路図.kicad_sch
kicad-local lint 回路図.kicad_sch          # departures from common design practice (protection on external lines, base resistors, supply capacitors...)
kicad-local render 基板.kicad_pcb iso      # 3D image without a GUI
kicad-local stats 基板.kicad_pcb           # part count, hole sizes, etc.
kicad-local drc 基板.kicad_pcb --strict    # also the 3 things DRC misses even at "0 violations" (pads without a net, tracks thinner than the netclass, stale zone fills)
kicad-local why 基板.kicad_pcb             # for each unrouted pair, "why it can't be routed" (keepouts, copper of other nets, board outline), per layer
kicad-local view 基板.kicad_pcb --drc --net GND   # annotated 2D image (highlighted nets, numbered DRC violations)
kicad-local autoroute 入力.kicad_pcb 出力.kicad_pcb   # autoroute the whole board with Freerouting, then refill zones and run DRC
kicad-local finish outline|zone|stitch|islands|widen 入力 出力 ...   # board outline, zones, stitching vias, island removal, widening tracks from current

# Move parts and route (steps can also come from stdin)
kicad-local route 基板.kicad_pcb 出力.kicad_pcb - --instructions 手順.md <<'EOF'
findspace R10 0.25                  # candidate places where it fits
drag R10 1,0                        # moving it reroutes the connected tracks around it and moves silkscreen text out of the way
autoroute R10.2 C5.1                # automatically find a path with at most 1 via
check                               # DRC on the spot
EOF

kicad-local sch 入力.kicad_sch 出力.kicad_sch 手順   # edit a schematic without a GUI (after saving: connectivity and BOM diff, ERC)
kicad-local pcbsync 回路図 基板 出力                # push schematic changes to the board
kicad-local fabdiff 前.kicad_pcb 後.kicad_pcb 比較/  # compare fabrication output layer by layer, with diff images and a list of locations
kicad-local sim run 手順.toml --out 結果/            # build SPICE automatically from the schematic and simulate (ngspice)
kicad-local review 新しい版 前の版 --out 確認/        # ERC, DRC, 3D, diffs and simulation in one run → 報告.md (report)
```

With `--instructions`, it also writes a step-by-step guide that lets a person reproduce the same operations in the KiCad GUI.
**Using it from AI or scripts**: drc, erc, why, view, stats, autoroute and finish output machine-readable results with `--json`, and drc, erc and why return
exit code 1 when something needs checking (so the next decision can be made from the result without parsing text). The 10.0.6 version runs without a display (DISPLAY).
For details see [docs/guide.md](docs/guide.md), for the tools [tools/README.md](tools/README.md), and for circuit simulation [tools/ksim/README.md](tools/ksim/README.md).

## AI chat in the PCB editor (10.0.6 version)

In the PCB editor, View ("表示") → Panels ("パネル") → AI Chat opens an AI chat panel on the right. You can ask it to look things up or make changes in the open board.

- The AI reads and edits the open board (10.0.6 uses KiCad's IPC API, 8.0.9 uses the Python inside KiCad). **By default, any code that changes the board is shown to you for confirmation every time before it runs**.
  Checking "AI のコードをたずねずに実行する" ("run AI code without asking") at the bottom of the panel skips the confirmation (a warning is shown when you turn it on. It is not saved, so it goes back to asking every time after you close KiCad. The code that was run stays in the chat).
  Changes that were run can be reverted with a single Undo ("元に戻す"), and changes from a run that ended in an error are rolled back.
- DRC and images work by saving a copy of the board and running kicad-cli on it (the open board and its file are not changed).
- It uses **your own Anthropic API key**. You are asked for it the first time, and it is stored in the OS key store (Credential Manager on Windows, etc.).
- **The Windows version and the Linux tar.gz bundle the Python and Claude Agent SDK for the AI** (`<KiCad>/ai-runtime`; `build/ai-runtime` on Linux). No setup or download is needed
  (the conversation with the AI itself needs an Internet connection).
- When built from source, the first time you open it you are asked whether to create a dedicated Python environment (needs Python 3.10 or later and an Internet connection).
  To set it up by hand: `python3 <KiCad の scripting>/kicad_ai/setup_ai.py` (`<KiCad の scripting>` = KiCad's scripting directory).
- If KiCad's API server is stopped, you are asked whether to enable it (the same setting as Preferences ("設定") → Plugins ("プラグイン")).

How it works: the panel (`pcbnew/widgets/panel_ai_chat.cpp`) only shows the conversation and handles approvals; the exchange with the AI is done by a broker in a separate process
(`scripting/kicad_ai/bridge.py`). There are 4 tools: `get_board` (overview and selection), `run_python` (kipy code, with approval), `check` (DRC) and `render` (images).

## Included changes

For each version, the change list gives the source (upstream commit or MR), the adjustments made for that version, and what was verified.

- [10.0.6](patches/10.0.6/README.md): this repository's improvements (headless routing commands, location and layer in DRC JSON, rerouting around moved parts, Gerber comparison, etc.) and **11 upstream MRs** (errors in ODB++ and IPC-2581 fabrication output, variant support in the schematic parity check, a file for `--theme`, etc.)
- [8.0.9](patches/8.0.9/README.md): 00–08 (19 router fixes, features backported from KiCad 9–11, etc.)

No feature that changes the file format is included.

## Installing (Linux, prebuilt)

tar.gz files for Ubuntu 24.04 (x86_64; Ubuntu on WSL2 works too) are on the Releases page for both 10.0.6 and 8.0.9
(`kicad-ja-plus-<version>-linux-x86_64.tar.gz` and `.sha256`).
They contain the improved kicad-cli, the GUI (`kicad`, `pcbnew`, `eeschema` and others), the kifaces, the pcbnew Python module, icons,
translations (Japanese and others), the headless tools, **the Python and Claude Agent SDK for the AI chat (`build/ai-runtime`)**, and **Freerouting with its own Java runtime (`build/freerouting`)**.
Shared components such as wxWidgets, OpenCASCADE and ngspice, and the part libraries and 3D models, come from the upstream KiCad installed on the system.

1. Install upstream KiCad **of the same series** from the PPA

   ```
   sudo add-apt-repository ppa:kicad/kicad-10.0-releases     # for 8.0.9: ppa:kicad/kicad-8.0-releases
   sudo apt install kicad python3-numpy python3-matplotlib python3-pil
   ```

2. Extract it anywhere and add `bin/` to PATH

   ```
   sha256sum -c kicad-ja-plus-10.0.6-linux-x86_64.tar.gz.sha256
   tar xzf kicad-ja-plus-10.0.6-linux-x86_64.tar.gz
   export PATH="$PWD/kicad-ja-plus-10.0.6-linux-x86_64/bin:$PATH"
   kicad-local help
   kicad-ja-plus-10.0.6-linux-x86_64/package/check-linux.sh   # checks the contents and that things run (tens of seconds)
   kicad-local test        # regression tests. If everything is OK, the installation is correct
   ```

3. Start the GUI with `pcbnew-ja` (PCB editor), `eeschema-ja` (schematic editor) or `kicad-ja` (project manager)
   (on WSL2 it appears through WSLg). From Python: `kicad-python -c "import pcbnew; print(pcbnew.Version())"`.

- The launchers in `bin/` use the `build/` inside the extracted folder automatically. To use them from another `bin/` (e.g. a git clone of this repository),
  set `export KICAD_LOCAL_BUILD=<extracted folder>/build`.
- If the upstream KiCad is from another series (e.g. the KiCad 8 PPA with the 10.0.6 package), install the missing shared components with
  `sudo apt install $(cat DEPENDS-apt.txt)`. `kicad-local test` and some tools compare against the system `kicad-cli`, so they fail unless the series match.

For whole-board autorouting (`kicad-local autoroute`), [Freerouting](https://github.com/freerouting/freerouting) 2.4.1 (GPL-3.0)
and a minimal Java runtime just for it (jlinked from Temurin 25) are bundled (`build/freerouting/`; origin and licenses in its README.txt).
It works on systems without Java. To use another jar or java, set `--jar`, `FREEROUTING_JAR` or `FREEROUTING_JAVA`.
The Windows version has the same in `<KiCad>\freerouting\`, started with `<KiCad>\bin\freerouting.cmd` (Freerouting in the Start menu).
When building from source, provide Java 25 or later and the Freerouting jar yourself (jar location with `--jar` or `FREEROUTING_JAR`).

## Building (Linux)

1. Install the build dependencies (Ubuntu/Debian. The list is in [patches/10.0.6/builddeps.txt](patches/10.0.6/builddeps.txt))

   ```
   sudo apt install $(cat patches/10.0.6/builddeps.txt)     # for 8.0.9: patches/8.0.9/notes/builddeps.txt
   ```

2. Fetch the KiCad source and apply the changes (default location is `~/src/kicad-<version>`)

   ```
   ./apply.sh 10.0.6        # or ./apply.sh 8.0.9
   ```

3. Build (settings verified on Ubuntu 24.04. For 8.0.9, replace 10.0.6 with 8.0.9)

   ```
   cmake -S ~/src/kicad-10.0.6 -B ~/src/build-10.0.6 -G Ninja -DCMAKE_BUILD_TYPE=Release \
         -DKICAD_BUILD_PNS_DEBUG_TOOL=ON -DKICAD_SCRIPTING_WXPYTHON=OFF -DKICAD_BUILD_I18N=OFF
   ninja -C ~/src/build-10.0.6 -j4 kicad-cli pcbnew_kiface eeschema_kiface cvpcb_kiface kicad_route kicad_gerber \
         pcbnew/_pcbnew.so s3d_plugin_vrml s3d_plugin_oce s3d_plugin_idf
   ```

   `KICAD_BUILD_PNS_DEBUG_TOOL=ON` is needed to build kicad_route (`qa/tools/pns/`).
   With 7 GB of memory, up to `-j5` works, and a full build takes about 20–30 minutes. Nothing is installed; it runs from the build directory
   (the entry points in `bin/` set `KICAD_RUN_FROM_BUILD_DIR` and so on).

4. Add `bin/` to PATH (or put the contents of `bin/` in `~/.local/bin`)

   ```
   export PATH="$PWD/bin:$PATH"
   kicad-local help
   ```

   Set the build directory with `KICAD_LOCAL_BUILD` (default `~/src/build-10.0.6`; for 8.0.9, `export KICAD_LOCAL_BUILD=~/src/build-8.0.9`).
   If you moved the source, set `KICAD_LOCAL_SRC`.
   The part libraries (footprints, symbols, 3D models) come from the upstream KiCad (same series) installed on the system (`/usr/share/kicad`).

## Tests

```
kicad-local test        # = test/regress.sh (a few tens of seconds)
```

Using the demo board (pic_programmer) and schematic (interf_u) that ship with KiCad 8.0.9, it runs routing steps and compares the results with the baseline (`test/baseline/`),
and also checks each tool individually.

## Folders

| Location | Contents |
|---|---|
| `patches/10.0.6/` | Changes for KiCad 10.0.6 (numbered patches; upstream MRs carry the author's name) |
| `patches/8.0.9/` | Changes for KiCad 8.0.9. `ALL-combined.patch` combines them all; the numbered patches are a historical record |
| `bin/` | Entry points (`kicad-local`, `kicad-route`, `kicad-gerber`, `kicad-cli-local`, `kicad-python`) and GUI launchers (`pcbnew-ja`, `eeschema-ja`, `kicad-ja`) |
| `tools/` | Python tools (review, lint, sch, sim, pcbsync, silkfix, etc.) |
| `test/` | Regression tests |
| `package/` | Scripts that build the distribution (`linux.sh`: makes a tar.gz from the build directory; `check-linux.sh`: checks an extracted package) |
| `installer/` | Windows installer (NSIS) |
| `docs/guide.md` | Guide (workflow, how to bring in features from the latest version, pitfalls) |

## License

GPL-3.0-or-later (same as KiCad). KiCad itself is by the KiCad developers, and the authors of changes taken from upstream are recorded in each patch.
