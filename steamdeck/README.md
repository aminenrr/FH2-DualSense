# FH2 DualSense on the Steam Deck (Linux / Proton)

A port of [aminenrr/FH2-DualSense](https://github.com/aminenrr/FH2-DualSense) for the Steam Deck and other Linux
setups that run the PC version of Forza Horizon 2 under Proton. The original tool is a Windows app built on `hidapi`
and `sounddevice`. Under Proton the game can't see the DualSense's HID and audio devices that way, so this port
splits the work between the game and Linux:

* **`FH2DualSense.dll`** is an [FH2 Mod Platform](https://github.com/Trinculo54/fh2-mod-platform) mod. Inside the
  game it:
  * reads the game's four rumble levels (both motors and both triggers) where the PC runtime hands them to
    `IGamepad::put_Vibration`, found by byte pattern, so it works on runtime versions 0.9.10 and 1.0.1;
  * taps the car's own sound buses in FMOD (the original's `fh2_fmodtap.c`, same RVAs, game build 1.0.0.64);
  * mixes the haptics signal and shares everything through `/dev/shm/fh2-dualsense.shm`.
* **`linux/fh2-dualsense.py`** is a helper that runs in SteamOS and uses only the Python standard library:
  * **Adaptive triggers, over Bluetooth or USB:** it writes DualSense output reports to `/dev/hidrawN` with only
    the trigger bytes valid. Steam Input keeps the rumble, lightbar and everything else. Bluetooth reports carry
    the CRC32 the controller requires.
  * **HD haptics, USB only:** it plays the 4-channel stream to the controller's `SpeakerHaptic` sink with `pw-cat`.
    On channels 3 and 4 that sink drives the actuators. Your default audio output (TV, headphones, AirPods) stays
    where it was.
  * It exits when the game closes and resets the triggers on the way out.
* **PlayStation button prompts:** the original's `prompts/fh2_ps_install.py`, unchanged. It runs fine on Linux.

Settings > MODS gets four rows: triggers on or off, HD haptics on or off, trigger force, and haptics strength.

> **Disclaimer.** This port is unofficial and unsupported. You install and run it at your own risk and
> responsibility. Don't report problems with it to the game's or the runtime's authors; open an issue in this fork.

## Requirements

* The PC runtime (tested with 0.9.10 and 1.0.1) with the FH2 Mod Platform (2.7.x) installed, and game build 1.0.0.64.
* The launch option `WINEDLLOVERRIDES="d3dcompiler_47=n,b"`, which the Mod Platform needs anyway.
* A DualSense or DualSense Edge. Triggers work over Bluetooth or USB. Haptics need the USB cable (a dock works).
* SteamOS 3.x. It needs `python3`, `pw-cat` and `wpctl`, which SteamOS already has. No root is needed: the Deck's
  `hidraw` nodes are writable by the logged-in user.

## Install

1. Build the DLL with `./build.sh <clone of fh2-mod-platform>`, which needs llvm-mingw, or take it from the
   release.
2. With the game closed, run `./install.sh install "<runtime folder>" "<Forza Horizon 2 folder>"`. It copies the
   DLL into `fh2-mods/` and installs the PlayStation prompts, backing up the originals.
3. In Steam > Forza Horizon 2 > Properties > Launch options, put:
   `WINEDLLOVERRIDES="d3dcompiler_47=n,b" /full/path/to/steamdeck/fh2-dualsense-launch.sh %command%`
   If you already have overrides, merge them, e.g. `winmm,dxgi,d3dcompiler_47=n,b` with Extra Options and ReShade.
4. Connect the controller before you start the game.

To uninstall, run `./install.sh uninstall …` and take the wrapper out of the launch options.

## Troubleshooting

* **`~/fh2-dualsense.log`** is the helper's log. It shows "DualSense on /dev/hidrawN (USB|BT)" and
  "HD haptics on -> …SpeakerHaptic__sink".
* **`fh2-mods/platform.log`** has the mod's side: "vibration levels hooked at azimuth+0x…" and "sound tap on bus:…".
* **No trigger effect over Bluetooth:** try `FH2DS_BT_FORMAT=pydualsense`, an alternative report layout, in front
  of the wrapper in the launch options.

## Credits

Triggers, haptics design, FMOD tap and prompts are by **aminenrr** (MIT), and this port keeps that license. The
Steam Deck port was written by consultoria-deta with Claude Code.
