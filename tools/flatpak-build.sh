#!/bin/bash
# Rebuilds this checkout as the sandboxed hu.irl.cameractrls Flatpak (branch master) and installs it for the user.
set -e
cd "$(dirname "$0")/../flatpak"
flatpak run --user org.flatpak.Builder --user --install --force-clean --disable-rofiles-fuse build-dir hu.irl.cameractrls.yml
flatpak make-current --user hu.irl.cameractrls master
systemctl --user restart cameractrlsd.service 2>/dev/null || true
# Launch the installed GTK app once, headless, and fail if it dies on import.
export XDG_RUNTIME_DIR=/run/user/1000
log=$(mktemp)
env -u WAYLAND_DISPLAY GDK_BACKEND=x11 timeout -s INT 15 gamescope --backend headless -W 1280 -H 960 -- \
  flatpak run --user --socket=x11 --nosocket=wayland --command=cameractrlsgtk4.py hu.irl.cameractrls >"$log" 2>&1 || true
if grep -qE "Traceback|ModuleNotFoundError|ImportError" "$log"; then
  echo "flatpak-build: the installed GTK app failed to start:"; grep -E "Traceback|Error" "$log" | head -5; rm -f "$log"; exit 1
fi
rm -f "$log"; echo "flatpak-build: installed GTK app starts"
