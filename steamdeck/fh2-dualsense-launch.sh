#!/bin/bash
# Steam launch wrapper: starts the FH2 DualSense helper, then the game.
#   Launch options:  WINEDLLOVERRIDES="d3dcompiler_47=n,b" /path/to/fh2-dualsense-launch.sh %command%
# (keep any other overrides you already use, e.g. "winmm,dxgi,d3dcompiler_47=n,b" with Extra Options and ReShade)
# The helper waits for the game, exits by itself when it closes, and logs to ~/fh2-dualsense.log.
HERE=$(cd "$(dirname "$0")" && pwd)
rm -f /dev/shm/fh2-dualsense.shm
setsid python3 -I "$HERE/fh2-dualsense.py" >> "$HOME/fh2-dualsense.log" 2>&1 < /dev/null &
exec "$@"
