# FH2 DualSense

> **Steam Deck / Linux (Proton):** see [`steamdeck/`](steamdeck/README.md): adaptive triggers over Bluetooth or USB, HD haptics over USB, PlayStation prompts, on runtime versions 0.9.10 and 1.0.1. Unofficial and unsupported; use at your own risk.

Adaptive triggers, HD haptics and PlayStation button prompts on a DualSense controller for Forza Horizon 2 running through Azimuth.

* **Adaptive triggers:** the game's own trigger rumble levels are read from Azimuth and sent to the DualSense triggers.
* **HD haptics:** the car's engine, road, skid, impact and shift sounds are captured from the game's audio engine and played through the DualSense haptic actuators.
* **PlayStation button prompts:** swaps the game's Xbox button prompts for PlayStation ones, and the controller drawing in Settings > Controls and on the loading screen for a DualSense. Your original files are backed up and can be restored at any time.
* **Small control window:** turn each feature on or off, tune haptics strength, and launch the game from the same window.
  
   ![FH2 DualSense window](screenshot.jpg)
  
## Requirements

* Windows with Python 3 (python.org, tick "Add python.exe to PATH").
* A DualSense controller connected by **USB cable** (haptics need USB).
* An xinput emulator so the dualsense work with the game (steam input or DS4win or DSX ... )
* Azimuth and your own copy of Forza Horizon 2, game build 1.0.0.64. Other builds are not supported, and the mod will say so in its log instead of hooking.
* In Windows Sound settings: Speakers (DualSense Wireless Controller) > Configure > Quadraphonic, volume 100.

## Install and run

1. Download the repository (Code > Download ZIP) and extract it anywhere.
2. Double-click `FH2 DualSense.bat`. It asks for administrator rights, and the first run installs the Python packages `hidapi`, `sounddevice`, `numpy` and `scipy`.
3. Press **Browse** once and pick `azimuth.exe` (or `FH2 DLC Launcher.exe`).
4. Press **PLAY**. The game starts, then triggers and haptics switch on by themselves. The status dots turn green when each part is live.
5. Press **STOP** or close the window to reset the triggers and bring the normal rumble back.

The first run also creates an `FH2 DualSense` shortcut next to the batch file. Use that from then on.

## PlayStation button prompts

Close the game first. There are two ways to install them.

Both ways ask for your `Anthem_1.0.0.64_x64__8wekyb3d8bbwe` game folder (the one that contains `Mount`). It doesn't have to be next to `azimuth.exe`.

**From the control window:** press **Install PlayStation buttons** (or **Restore Xbox buttons** to undo) and pick the `Anthem_1.0.0.64_x64__8wekyb3d8bbwe` folder in the window that opens. The folder is remembered and shown in the card, and the card says whether the PlayStation buttons are on.

**From the `prompts` folder:** double-click `Install PlayStation buttons.bat` and paste the path of your `Anthem_1.0.0.64_x64__8wekyb3d8bbwe` folder when it asks, or drag that folder onto the .bat. Run `Restore Xbox buttons.bat` the same way to undo.

What it does:

* It edits four files in your game folder: `Mount\media\UI.zip` and `ControllerIcons.zip`, `Help_Buttons.zip` and `UI_Symbols.zip` in `Mount\media\ui\textures\data_bound\`.
* The originals are saved next to them as `.bak`. Restoring puts those back.
* It only works on game build 1.0.0.64. If your files don't match, it stops and changes nothing.
* It needs only Python. Nothing has to be pip installed for it.

See `prompts/README.md` for the folder layout and how to change the art.

## Why it asks for administrator rights

The mod reads a small amount of memory from `azimuth.exe` (the trigger rumble values) and loads a small DLL into it (to listen to the game's car sounds). Windows requires elevation for that. Nothing is written to disk by the memory hook, and it disappears when the game closes. Read the code in `app/azimuth_hook.py` and `source/fh2_fmodtap.c` if you want to check.

Because the mod works this way, some antivirus programs may flag `fh2_fmodtap2.dll` or the launcher. If you would rather not trust the prebuilt DLL, build it yourself from `source/fh2_fmodtap.c` (see below).

## Files

* `app/` the programs: the control window, trigger and haptics scripts, the Azimuth hook, and the prebuilt `fh2_fmodtap2.dll`. `settings.json` is created here from your menu choices.
* `source/` source of the sound tap DLL.
* `prompts/` the PlayStation prompts installer, the raw prompt and DualSense diagram PNGs (`raw/`), the generated texture data (`build/`), the scripts that make it (`tools/`), and Kenney's source SVGs and license (`third_party/`).

## Building the DLL yourself

`source/fh2_fmodtap.c` only includes standard Windows headers. Build it as a 64 bit DLL with an x64 C compiler and save the result as `app/fh2_fmodtap2.dll`. For example with MinGW-w64:

```
x86_64-w64-mingw32-gcc -shared -O2 -o fh2_fmodtap2.dll source/fh2_fmodtap.c
```

This command is a starting point, so adjust flags for your toolchain if it complains.

## Troubleshooting

* A log named `fmodtap_log.txt` is written next to the DLL when the sound tap loads. It says whether the game build matched.
* No haptics: check the Quadraphonic setting, USB connection and the volume of the DualSense speaker device.
* If the controller does nothing after a crash, press STOP or close and reopen the window to reset the triggers.

## Credits

* Button prompt art: [Kenney Input Prompts](https://kenney.nl/assets/input-prompts) by Kenney (www.kenney.nl), CC0 / public domain. The license is in `prompts/third_party/kenney_input_prompts/License.txt`.
* DualSense controller diagram: made for this project.

## Disclaimer

This project is not affiliated with or endorsed by Microsoft, Playground Games, Sony or the Azimuth developers. It contains no game files and no Azimuth files: the prompts installer edits the files from your own copy of the game on your PC and ships only its own art and patch data. You need your own legitimate copy of the game and Azimuth. Use it at your own risk.

Forza Horizon is a trademark of Microsoft. PlayStation, DualSense and the PlayStation button symbols are trademarks of Sony Interactive Entertainment. They are named and shown here only to describe what the mod does.

## AI disclosure

This project was built with the help of AI (Claude by Anthropic). AI was used to go through the game files and Azimuth to look for ways to hook into the game, such as reading the trigger rumble values and capturing the car sounds, and to help write and debug the code. AI also helped work out the game's texture format for the button prompts, write the prompts installer, and draw the DualSense diagram. The button icons themselves are Kenney's, not AI generated. I tested the mod myself on my own setup.

The hooks depend on specific versions (Azimuth 0.9.x and game build 1.0.0.64), so other versions may not work. AI written code can contain mistakes, so please read the code before running it, especially because the launcher asks for administrator rights.

## License

MIT, see `LICENSE`.
