# PlayStation buttons for FH2

Replaces Forza Horizon 2's Xbox button prompts with PlayStation ones, and swaps the controller drawing in **Settings > Controls** and on the **loading screen** for a DualSense.

## No game files included

This folder holds no game files. The installer opens the UI zips from **your own** game, puts our pixel data into the button textures, and saves the zips back. Your original files are kept next to them as `.bak`.

| What | Where it comes from |
|---|---|
| Button prompts | [Kenney Input Prompts](https://kenney.nl/assets/input-prompts), CC0 (public domain). Source SVGs and license are in `third_party/kenney_input_prompts/` |
| DualSense diagram | Made for this project. PNGs are in `raw/dualsense_diagram/` |
| Settings pointer lines | New line coordinates in `build/xaml_patch.json`. Only our replacement lines are stored, plus a checksum of each original line so the installer can confirm it has the right game version |

## Install

Close the game first, then either:
- open the FH2 DualSense menu, press **Install PlayStation buttons** and pick your `Anthem_1.0.0.64_x64__8wekyb3d8bbwe` folder, or
- double-click `Install PlayStation buttons.bat` and paste that folder's path (or drag the folder onto the .bat).

The Anthem folder is the one that contains `Mount`. It can be anywhere; it doesn't need to sit next to `azimuth.exe`.

To undo, press **Restore Xbox buttons** or run `Restore Xbox buttons.bat` the same way. Your `.bak` originals are put back.

It only works with game build **1.0.0.64** (Azimuth). If your files are a different version, the installer stops and changes nothing.

### Files it touches
```
Mount\media\UI.zip                                  (Settings > Controls pointer lines)
Mount\media\ui\textures\data_bound\ControllerIcons.zip
Mount\media\ui\textures\data_bound\Help_Buttons.zip
Mount\media\ui\textures\data_bound\UI_Symbols.zip   (the controller diagram)
```

## Folders

```
raw/ps_prompts/hud/          in-game prompts (white, symbol cut out)
raw/ps_prompts/help/         small help-bar icons (gray disc, black symbol, like the game's own)
raw/ps_prompts/help_dark/    dark variant
raw/ps_prompts/help_light/   light variant
raw/dualsense_diagram/       DualSense drawing for Settings and the loading screen (512x512)
build/                       ready-made texture data the installer uses (generated from raw/)
tools/                       scripts that make build/ from raw/ (only needed to change the art)
third_party/                 Kenney's CC0 source SVGs and license
fh2_ps_install.py            the installer (standard Python, nothing to pip install)
fh2_swatch.py                FH2 texture layout and zip helpers
```

## Changing the art

Edit the PNGs in `raw/` (keep them 512x512, transparent background), then:

```
pip install pillow numpy etcpak
python tools/build_payloads.py build
```

and run the installer again. `tools/render_glyphs.py` regenerates `raw/ps_prompts/` from the Kenney SVGs (it also needs `cairosvg scipy`).

## Credits
- Button prompt art: **Kenney** (www.kenney.nl), Input Prompts 1.5, CC0.
- PlayStation, DualSense and the button symbols are trademarks of Sony Interactive Entertainment. Forza Horizon is a trademark of Microsoft. This is a fan project and isn't affiliated with or endorsed by either company.
