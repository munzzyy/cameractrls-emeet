#!/bin/bash
# walk2.sh <appdir> <prefix>: headless page-by-page screenshots of the GTK4 app
export XDG_RUNTIME_DIR=/run/user/1000
S=$(dirname "$0")
( env -u WAYLAND_DISPLAY GDK_BACKEND=x11 timeout -s INT 60 gamescope --backend headless -W 1280 -H 960 -- python3 "$S/pagewalk.py" "$1" "$2" >"$2.out" 2>"$2.err" & )
sleep 32
ls "$2"-page*.png 2>/dev/null | wc -l
grep -vE "^\[gamescope\]|^>|xkbcomp|^$|Locale not supported|fallback 'C' locale" "$2.err" | tail -8
