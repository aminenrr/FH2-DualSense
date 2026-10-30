#!/bin/bash
# FH2 DualSense for the Steam Deck: install / uninstall / status. Run with the game closed.
#   ./install.sh install   <azimuth folder> <game folder>
#   ./install.sh uninstall <azimuth folder> <game folder>
#   ./install.sh status    <azimuth folder> <game folder>
# <azimuth folder> holds azimuth.exe and the FH2 Mod Platform (fh2-mods/); <game folder> is the "Forza Horizon 2" folder
# with Mount/. Needs FH2DualSense.dll next to this script (see README for building it).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
CMD=$1; AZ=$2; GAME=$3
[ -n "$CMD" ] && [ -d "$AZ" ] && [ -d "$GAME" ] || { sed -n 3,6p "$0"; exit 1; }
if pgrep -x azimuth.exe >/dev/null; then echo "the game is running: close it first"; exit 1; fi
PROMPTS="$HERE/../prompts/fh2_ps_install.py"
case "$CMD" in
install)
  [ -d "$AZ/fh2-mods" ] || { echo "no fh2-mods/ in $AZ: install the FH2 Mod Platform first"; exit 1; }
  cp "$HERE/FH2DualSense.dll" "$AZ/fh2-mods/"
  chmod +x "$HERE/fh2-dualsense-launch.sh"
  python3 -I "$PROMPTS" install "$GAME"
  echo
  echo "Installed. In Steam > Forza Horizon 2 > Properties > Launch options, put:"
  echo "  WINEDLLOVERRIDES=\"d3dcompiler_47=n,b\" $HERE/fh2-dualsense-launch.sh %command%"
  echo "(merge the overrides with the ones you already have)"
  ;;
uninstall)
  rm -f "$AZ/fh2-mods/FH2DualSense.dll"
  python3 -I "$PROMPTS" uninstall "$GAME"
  echo "Removed. Take fh2-dualsense-launch.sh out of the launch options."
  ;;
status)
  ls -la "$AZ/fh2-mods/FH2DualSense.dll" 2>/dev/null || echo "DLL: not installed"
  python3 -I "$PROMPTS" status "$GAME" || true
  for h in /sys/class/hidraw/*; do grep -H HID_NAME "$h/device/uevent" 2>/dev/null; done
  ls -la /dev/hidraw* 2>/dev/null
  ;;
*) sed -n 3,6p "$0"; exit 1;;
esac
