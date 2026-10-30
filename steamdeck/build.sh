#!/bin/bash
# Builds FH2DualSense.dll with llvm-mingw (https://github.com/mstorsjo/llvm-mingw) against the FH2 Mod Platform header.
#   ./build.sh <path to a clone of https://github.com/Trinculo54/fh2-mod-platform>
set -e
HERE=$(cd "$(dirname "$0")" && pwd); PLATFORM=${1:?path to fh2-mod-platform}
x86_64-w64-mingw32-clang++ -std=c++20 -O2 -Wall -Wextra -shared -static \
  -I "$PLATFORM/include" -DFH2MOD_PLATFORM_VERSION='"2.7.3"' \
  -o "$HERE/FH2DualSense.dll" "$HERE/src/fh2_dualsense.cpp"
echo "built $HERE/FH2DualSense.dll"
