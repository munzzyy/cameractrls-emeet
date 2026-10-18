#!/bin/bash
# Checks for this fork against the real camera. Exits non-zero on any failure.
#   CAMERA=/dev/v4l/by-id/...  override the device (default: the first 328f:00e1 capture node)
#   SKIP_UI=1                  skip the headless GTK page walk (about 35 s)
# Reads the camera, writes only to a temp dir, and puts back any control it touches.
set -uo pipefail

REPO=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)
find_camera() {
    local d node link
    for d in /sys/class/video4linux/video*; do
        [ "$(cat "$d/index" 2>/dev/null)" = 0 ] || continue
        [ "$(cat "$d/device/../idVendor" 2>/dev/null):$(cat "$d/device/../idProduct" 2>/dev/null)" = 328f:00e1 ] || continue
        node=/dev/$(basename "$d")
        for link in /dev/v4l/by-id/*-video-index0; do
            [ "$(readlink -f "$link")" = "$node" ] && { echo "$link"; return; }
        done
        echo "$node"; return
    done
}
CAMERA=${CAMERA:-$(find_camera)}
[ -n "$CAMERA" ] || { echo "no EMEET C60E (328f:00e1) found, set CAMERA=/dev/videoN" >&2; exit 2; }
TMP=$(mktemp -d)
FAILS=0
CLI=(python3 -W error::DeprecationWarning "$REPO/cameractrls.py" -d "$CAMERA")

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
step() { if "$@"; then pass "$DESC"; else fail "$DESC"; fi; }

BRIGHTNESS_BEFORE=
LIGHT_BEFORE=
cleanup() {
    if [ -n "$LIGHT_BEFORE" ]; then
        "${CLI[@]}" -c "emeet_c60e_light=$LIGHT_BEFORE" 2>/dev/null
    fi
    if [ -n "$BRIGHTNESS_BEFORE" ]; then
        "${CLI[@]}" -c "brightness=$BRIGHTNESS_BEFORE" 2>/dev/null
    fi
    rm -rf "$TMP"
}
trap cleanup EXIT

cd "$REPO" || exit 1

DESC="py_compile every .py file"
step python3 - <<'PY'
import ast, pathlib, sys
bad = 0
for p in sorted(pathlib.Path('.').rglob('*.py')):
    try:
        ast.parse(p.read_text(encoding='utf-8'), str(p))
    except SyntaxError as e:
        print(f'  {p}: {e}'); bad += 1
sys.exit(1 if bad else 0)
PY

DESC="tests/test_emhid.py"
step python3 -I "$REPO/tests/test_emhid.py"

DESC="CLI -L lists the camera"
step bash -c '"$@" -L 2>&1 | grep -q "C60E"' _ python3 -W error::DeprecationWarning "$REPO/cameractrls.py"

LIST="$TMP/list.txt"
if "${CLI[@]}" -l >"$LIST" 2>"$TMP/list.err"; then pass "CLI -l"; else fail "CLI -l"; fi
DESC="CLI -l has no dead (min == max) controls"
step bash -c '! grep -q "zoom_continuous" "$1"' _ "$LIST"
DESC="CLI -l shows the EMEET C60E category"
step grep -q "Basic / EMEET C60E" "$LIST"
DESC="CLI -l stderr has no WARNING/ERROR/Traceback/Deprecation"
step bash -c '! grep -E "WARNING|ERROR|Traceback|Deprecat" "$1"' _ "$TMP/list.err"

# preset round trip, config goes to a temp dir
export XDG_CONFIG_HOME="$TMP/config"
INI="$XDG_CONFIG_HOME/hu.irl.cameractrls/$(basename "$CAMERA").ini"
BRIGHTNESS_BEFORE=$(awk '$1 == "brightness" {print $3}' "$LIST")
if [ -z "$BRIGHTNESS_BEFORE" ]; then
    fail "read brightness"
else
    "${CLI[@]}" -c preset=save_1 2>"$TMP/preset.err"
    "${CLI[@]}" -c preset=save_2_fmt 2>>"$TMP/preset.err"
    DESC="preset=save_1 stores no capture format"
    step bash -c 'awk "/^\[preset_1\]/{f=1;next} /^\[/{f=0} f" "$1" | grep -qE "^(pixelformat|resolution|fps) " && exit 1; exit 0' _ "$INI"
    DESC="preset=save_2_fmt stores the capture format"
    step bash -c 'awk "/^\[preset_2\]/{f=1;next} /^\[/{f=0} f" "$1" | grep -qE "^pixelformat "' _ "$INI"
    TEST_B=$(( BRIGHTNESS_BEFORE == 100 ? 101 : 100 ))
    "${CLI[@]}" -c "brightness=$TEST_B" 2>>"$TMP/preset.err"
    "${CLI[@]}" -c preset=load_1 2>>"$TMP/preset.err"
    NOW=$("${CLI[@]}" -l 2>/dev/null | awk '$1 == "brightness" {print $3}')
    DESC="preset load_1 puts brightness back ($TEST_B -> $BRIGHTNESS_BEFORE)"
    step test "$NOW" = "$BRIGHTNESS_BEFORE"
    DESC="preset round trip stderr has no WARNING/ERROR/Traceback/Deprecation"
    step bash -c '! grep -E "WARNING|ERROR|Traceback|Deprecat" "$1"' _ "$TMP/preset.err"
fi

# the light colour travels in presets
LIGHT_BEFORE=$("${CLI[@]}" -l 2>/dev/null | awk '$1 == "emeet_c60e_light" {print $3}')
if [ -z "$LIGHT_BEFORE" ]; then
    fail "read light colour"
else
    "${CLI[@]}" -c emeet_c60e_light=ff0000 2>"$TMP/light.err"
    "${CLI[@]}" -c preset=save_3 2>>"$TMP/light.err"
    "${CLI[@]}" -c emeet_c60e_light=0000ff 2>>"$TMP/light.err"
    "${CLI[@]}" -c preset=load_3 2>>"$TMP/light.err"
    NOW=$("${CLI[@]}" -l 2>/dev/null | awk '$1 == "emeet_c60e_light" {print $3}')
    DESC="preset load_3 puts the light back to ff0000 (got $NOW)"
    step test "$NOW" = ff0000
    DESC="light round trip stderr is empty"
    step test ! -s "$TMP/light.err"
    "${CLI[@]}" -c "emeet_c60e_light=$LIGHT_BEFORE" 2>/dev/null
fi

# remote buttons, step values, zoom factor and capture modes
DESC="CLI -l has the Remote category with the step buttons"
step bash -c 'grep -q "Basic / Remote" "$1" && grep -q "step_1x, step_3x, step_5x, step_7x, step_11x" "$1"' _ "$LIST"
DESC="Remote category has the Flip/Mirror/Effects note"
step grep -q "press Reset on the remote to clear them" "$LIST"
DESC="zoom factor text interpolates the measured table"
step python3 - "$REPO" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import cameractrls as c
want = {0: '0 (about 1.0x, Wide)', 165: '165 (about 1.5x, Wide)', 170: '170 (about 1.6x, Tele)',
        260: '260 (about 2.0x, Tele)', 400: '400 (about 3.2x, Tele)'}
bad = {z: c.emeet_zoom_text(z) for z, t in want.items() if c.emeet_zoom_text(z) != t}
assert c.emeet_zoom_text(253).startswith('253 (about 2.0x'), c.emeet_zoom_text(253)
assert c.emeet_zoom_factor(-5) == 1.0 and c.emeet_zoom_factor(999) == 3.2
sys.exit(1 if bad else 0)
PY

ZOOM_BEFORE=$("${CLI[@]}" -l 2>/dev/null | awk '$1 == "zoom_absolute" {print $3}')
FMT_BEFORE=$("${CLI[@]}" -l 2>/dev/null | awk '$1 == "resolution" {r=$3} $1 == "fps" {f=$3} END {print r, f}')
RES_BEFORE=${FMT_BEFORE% *}
FPS_BEFORE=${FMT_BEFORE#* }
"${CLI[@]}" -c zoom_absolute=253 2>"$TMP/learn.err"
"${CLI[@]}" --set-step 3x=253 2>>"$TMP/learn.err"
DESC="--set-step 3x=253 stores the value in [remote_steps]"
step bash -c 'awk "/^\[remote_steps\]/{f=1;next} /^\[/{f=0} f" "$1" | grep -qx "3x = 253"' _ "$INI"
DESC="-l shows the set step with its magnification"
step bash -c '"$@" -l | grep -q "3X 253 (2.0x, set by you)"' _ "${CLI[@]}"
"${CLI[@]}" -c "zoom_absolute=0,emeet_remote_step=step_3x" 2>>"$TMP/learn.err"
NOW=$("${CLI[@]}" -l 2>/dev/null | awk '$1 == "zoom_absolute" {print $3}')
DESC="the 3X button zooms to the stored value"
step test "$NOW" = 253
DESC="--set-step with an unknown step fails"
step bash -c '! "$@" --set-step 4x=10 2>/dev/null' _ "${CLI[@]}"
DESC="--set-step out of range fails"
step bash -c '! "$@" --set-step 3x=999 2>/dev/null' _ "${CLI[@]}"
DESC="--learn-step is gone"
step bash -c '! "$@" --learn-step 3x 2>/dev/null' _ "${CLI[@]}"
"${CLI[@]}" -c emeet_remote_zoom=zoom_in 2>>"$TMP/learn.err"
NOW=$("${CLI[@]}" -l 2>/dev/null | awk '$1 == "zoom_absolute" {print $3}')
DESC="Zoom in moves zoom_absolute by 10 (253 -> 263)"
step test "$NOW" = 263
"${CLI[@]}" -c "zoom_absolute=${ZOOM_BEFORE:-0}" 2>>"$TMP/learn.err"
DESC="step/zoom stderr has no WARNING/ERROR/Traceback/Deprecation"
step bash -c '! grep -vE "no step 4x" "$1" | grep -E "WARNING|ERROR|Traceback|Deprecat"' _ "$TMP/learn.err"

DESC="remote push reader emits one event for a push report and ignores the noise"
step python3 -W error - "$REPO" <<'PY'
import os, sys, threading
sys.path.insert(0, sys.argv[1])
import emhid
r, w = os.pipe()
os.set_blocking(r, False)
hits = []
stop = threading.Event()
t = threading.Thread(target=emhid.read_pushes, args=(r, lambda: hits.append(1), stop, 0.05))
t.start()
pad = lambda b: b + bytes(emhid.REPORT_LEN - len(b))
for rep in (pad(bytes((9, 2, 2, 3, 0, 4, 0, 4))), pad(b'\x01\x04\x00\x11'), pad(bytes((9, 4, 0, 0x11, 0, 0, 0, 0))), pad(bytes((9, 4, 0, 0x12))), b'\x09'):
    os.write(w, rep)
import time
time.sleep(0.3)
stop.set()
t.join(2)
os.close(r); os.close(w)
assert not t.is_alive() and hits == [1], hits
PY
DESC="remote watcher on a missing node logs at most debug and exits"
step python3 -W error - "$REPO" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import emhid
w = emhid.RemoteWatcher(lambda: None, path='/nonexistent/hidraw99')
w.start()
w.join(2)
assert not w.is_alive()
PY

MODE_OUT=$("${CLI[@]}" -c capture_mode=720p60 2>&1)
if echo "$MODE_OUT" | grep -qiE "busy|EBUSY"; then
    echo "SKIP  capture mode checks (the camera is streaming, set a mode when it is free)"
else
    DESC="capture_mode=720p60 sets MJPG 1280x720 at 60"
    step bash -c '"$@" -l | awk "\$1 == \"pixelformat\" {p=\$3} \$1 == \"resolution\" {r=\$3} \$1 == \"fps\" {f=\$3} END {exit !(p == \"MJPG\" && r == \"1280x720\" && f == \"60\")}"' _ "${CLI[@]}"
    "${CLI[@]}" -c preset=save_3_fmt 2>>"$TMP/mode.err"
    DESC="a preset saved with the format carries the capture mode"
    step bash -c 'awk "/^\[preset_3\]/{f=1;next} /^\[/{f=0} f" "$1" | grep -qx "resolution = 1280x720"' _ "$INI"
    DESC="CLI -l lists only capture modes the device has"
    step bash -c '"$@" -l | grep -E "^ capture_mode" | grep -q "4k30, 1440p30, 1080p60, 1080p30, 720p60"' _ "${CLI[@]}"
    "${CLI[@]}" -c "resolution=$RES_BEFORE" 2>>"$TMP/mode.err"
    "${CLI[@]}" -c "fps=$FPS_BEFORE" 2>>"$TMP/mode.err"
    FMT_NOW=$("${CLI[@]}" -l 2>/dev/null | awk '$1 == "resolution" {r=$3} $1 == "fps" {f=$3} END {print r, f}')
    DESC="capture format put back ($RES_BEFORE at $FPS_BEFORE)"
    step test "$FMT_NOW" = "$RES_BEFORE $FPS_BEFORE"
fi

# a preset from another build may name controls this device doesn't have or this fork hides
printf '\n[preset_4]\nzoom_continuous = 0\nbogus_ctrl = 1\n' >> "$INI"
"${CLI[@]}" -c preset=load_4 >"$TMP/unknown.out" 2>&1
DESC="loading a preset with unknown/hidden keys is silent"
step test ! -s "$TMP/unknown.out"

"${CLI[@]}" --name-preset "2=Low light" 2>"$TMP/names.err"
DESC="preset names show up in -l"
step bash -c '"$@" -l | grep -q "names: 2=Low light"' _ "${CLI[@]}"
"${CLI[@]}" --name-preset "2=" 2>>"$TMP/names.err"
DESC="clearing a preset name removes it from -l"
step bash -c '! "$@" -l | grep -q "names:"' _ "${CLI[@]}"
DESC="preset name stderr is empty"
step test ! -s "$TMP/names.err"

DESC="CLI -l has the Check remote button and the Last remote check row"
step bash -c 'grep -q "emeet_remote_check" "$1" && grep -q "emeet_remote_check_info = " "$1"' _ "$LIST"
DESC="remote check classifier + --remote-check on fake frames (tools/test_remote_check.py)"
step env CAMERA="$CAMERA" python3 -W error "$REPO/tools/test_remote_check.py"

DESC="tools/test_measure.py (measure.py on synthetic images, no device)"
step python3 -W error::DeprecationWarning "$REPO/tools/test_measure.py"

# the four presets ship in tools/emeet-presets.ini
QCONF="$TMP/qconfig"
mkdir -p "$QCONF/hu.irl.cameractrls"
QINI="$QCONF/hu.irl.cameractrls/$(basename "$CAMERA").ini"
cp "$REPO/tools/emeet-presets.ini" "$QINI"
DESC="the shipped preset names show up in -l"
step bash -c 'XDG_CONFIG_HOME="$1" "${@:2}" -l | grep -q "names: 1=Stream, 2=60fps (light), 3=Quality, 4=Natural"' _ "$QCONF" "${CLI[@]}"
DESC="Quality saves 4K30 MJPG, Natural saves no format"
step python3 - "$QINI" <<'PY'
import configparser, sys
c = configparser.ConfigParser(interpolation=None); c.read(sys.argv[1])
q, n = c['preset_3'], c['preset_4']
ok = (q['pixelformat'], q['resolution'], q['fps']) == ('MJPG', '3840x2160', '30')
ok = ok and not any(k in n for k in ('pixelformat', 'resolution', 'fps'))
ok = ok and q['white_balance_automatic'] == '0' and n['white_balance_automatic'] == '0'
sys.exit(0 if ok else 1)
PY
DESC="4K30 MJPG is something this camera lists"
step bash -c 'v4l2-ctl -d "$1" --list-formats-ext | awk "/MJPG/{f=1} /YUYV/{f=0} f" | grep -q "3840x2160"' _ "$CAMERA"
RESTORE=$(python3 - "$LIST" "$QINI" <<'PY'
import configparser, re, sys
c = configparser.ConfigParser(interpolation=None); c.read(sys.argv[2])
keys = list(dict.fromkeys(list(c['preset_3']) + list(c['preset_4'])))
keys = [k for k in keys if k not in ('pixelformat', 'resolution', 'fps')]
live = {}
for line in open(sys.argv[1]):
    m = re.match(r'^ (\S+) = (\S+)\t\(.*', line)
    if m and 'inactive' not in line:
        live[m.group(1)] = m.group(2)
auto = ('auto_exposure', 'white_balance_automatic', 'focus_automatic_continuous')
order = [k for k in auto if k in live] + [k for k in keys if k in live and k not in auto]
print(','.join(f'{k}={live[k]}' for k in order))
PY
)
python3 - "$QINI" "$TMP/q_nofmt.ini" <<'PY'
import configparser, sys
c = configparser.ConfigParser(interpolation=None); c.read(sys.argv[1])
for s in ('preset_3', 'preset_4'):
    for k in ('pixelformat', 'resolution', 'fps'):
        c.remove_option(s, k)
c.write(open(sys.argv[2], 'w'))
PY
cp "$TMP/q_nofmt.ini" "$QINI"
for N in 3 4; do
    XDG_CONFIG_HOME="$QCONF" "${CLI[@]}" -c "preset=load_$N" >"$TMP/qload$N.out" 2>&1
    DESC="preset load_$N (controls only) is silent"
    step test ! -s "$TMP/qload$N.out"
done
XDG_CONFIG_HOME="$QCONF" "${CLI[@]}" -c "preset=load_3" >/dev/null 2>&1
DESC="preset 3 sets sharpness 200 and aperture priority"
step bash -c '"$@" -l | grep -qE "^ sharpness = 200" ' _ "${CLI[@]}"
"${CLI[@]}" -c "$RESTORE" >"$TMP/qrestore.out" 2>&1
DESC="live controls put back after the preset loads (stderr: $(head -c 80 "$TMP/qrestore.out"))"
step test ! -s "$TMP/qrestore.out"

PROBE=$(python3 "$REPO/tools/stream_probe.py" "$CAMERA" 2>&1)
DESC="auto_exposure write is ignored without a stream and sticks with one ($(echo "$PROBE" | tr '\n' ' '))"
step bash -c 'grep -qE "^IDLE_UNCHANGED=(1|skipped)" <<<"$1" && grep -qx "STREAM_CHANGED=1" <<<"$1" && grep -qx "RESTORED=1" <<<"$1"' _ "$PROBE"
DESC="exposure retry, luma and dark watchdog tests (tools/test_exposure.py)"
step python3 -W error::DeprecationWarning "$REPO/tools/test_exposure.py"
DESC="pad mapper tests (tools/test_pad.py)"
step python3 -W error::DeprecationWarning "$REPO/tools/test_pad.py"

PADLIST=$(python3 "$REPO/cameraptzgame.py" -l 2>"$TMP/padlist.err")
if ls /dev/input/js* >/dev/null 2>&1; then
    DESC="cameraptzgame -l lists the pad that is plugged in"
    step test -n "$PADLIST"
else
    echo "SKIP  cameraptzgame -l pad listing (no /dev/input/js*)"
fi
DESC="cameraptzgame -l stderr is empty"
step test ! -s "$TMP/padlist.err"

MAP=$(python3 "$REPO/cameraptzgame.py" -m -d "$CAMERA" 2>&1)
DESC="cameraptzgame -m prints the C60E map"
step bash -c 'echo "$1" | grep -q "Zoom step 11X" && echo "$1" | grep -q "Back + Start"' _ "$MAP"

python3 "$REPO/cameraptzgame.py" -c 0 -d "$CAMERA" >"$TMP/pad.out" 2>"$TMP/pad.err" &
PAD_PID=$!
sleep 3
kill -TERM "$PAD_PID" 2>/dev/null
wait "$PAD_PID"; PAD_RC=$?
DESC="cameraptzgame exits 0 on SIGTERM and releases the device"
step bash -c '[ "$1" = 0 ] && ! fuser "$2" 2>/dev/null | grep -qw "$3"' _ "$PAD_RC" "$CAMERA" "$PAD_PID"
DESC="cameraptzgame stderr has no Traceback/WARNING/ERROR"
step bash -c '! grep -E "WARNING|ERROR|Traceback" "$1"' _ "$TMP/pad.err"

if [ "${SKIP_UI:-0}" = 1 ]; then
    echo "SKIP  headless page walk (SKIP_UI=1)"
else
    export XDG_RUNTIME_DIR=/run/user/1000
    # only one headless gamescope at a time, give another user of it a while to finish
    for _ in $(seq 1 60); do pgrep -x gamescope-wl >/dev/null || break; sleep 3; done
    if pgrep -x gamescope-wl >/dev/null; then
        fail "headless page walk (another gamescope kept running)"
    else
        rm -f "$XDG_RUNTIME_DIR"/gamescope-0 "$XDG_RUNTIME_DIR"/gamescope-0.lock "$XDG_RUNTIME_DIR"/gamescope-0-ei*
        # the preview window closes through SDL_QUIT, like the X button
        QOUT=$(env -u WAYLAND_DISPLAY GDK_BACKEND=x11 SDL_VIDEODRIVER=x11 timeout 60 gamescope --backend headless -W 1280 -H 960 -- bash -c '
            python3 -W error::DeprecationWarning "$1/cameraview.py" -d "$2" -s 1280x720 --quit-after 4 2>"$3"
            echo "EXIT=$?"; H=""; for p in $(fuser "$2" 2>/dev/null); do tr "\0" " " </proc/${p//[!0-9]/}/cmdline 2>/dev/null | grep -q -- "--watch-remote" || H="$H${p//[!0-9]/}"; done; echo "HOLDERS=$H"' _ "$REPO" "$CAMERA" "$TMP/view.err" 2>/dev/null | grep -E "^(EXIT|HOLDERS)=")
        DESC="cameraview --quit-after exits 0 and releases the device"
        step bash -c 'echo "$1" | grep -qx "EXIT=0" && echo "$1" | grep -qx "HOLDERS="' _ "$QOUT"
        DESC="cameraview stderr has no Traceback/WARNING/ERROR/Deprecation"
        step bash -c '! grep -E "WARNING|ERROR|Traceback|Deprecat" "$1"' _ "$TMP/view.err"
        sleep 2
        SHOTS="${SHOTS:-$TMP/shots}"
        mkdir -p "$SHOTS"
        PNGS=$("$REPO/tools/walk2.sh" "$REPO" "$SHOTS/check" | head -1)
        DESC="page walk took 5 screenshots (got $PNGS) in $SHOTS"
        step test "$PNGS" = 5
        sleep 2
        for _ in $(seq 1 60); do pgrep -x gamescope-wl >/dev/null || break; sleep 3; done
        rm -f "$XDG_RUNTIME_DIR"/gamescope-0 "$XDG_RUNTIME_DIR"/gamescope-0.lock "$XDG_RUNTIME_DIR"/gamescope-0-ei*
        env -u WAYLAND_DISPLAY GDK_BACKEND=x11 timeout 60 gamescope --backend headless -W 1280 -H 960 -- bash -c '
            python3 "$1/tools/padwalk.py" "$1" "$2" >"$2.out" 2>"$2.err"' _ "$REPO" "$SHOTS/pad" >/dev/null 2>&1
        DESC="header shows the pad state, the Button map entry is there and the map dialog was shot"
        step bash -c 'grep -q "^PADLABEL=Pad: " "$1.out" && grep -qx "MAPBUTTON=1" "$1.out" && [ "$(ls "$1"-*.png | wc -l)" = 2 ]' _ "$SHOTS/pad"
        DESC="pad walk stderr has no Traceback/WARNING/ERROR/Deprecation"
        step bash -c '! grep -vE "^\[gamescope\]|Locale not supported|fallback .C. locale" "$1.err" | grep -E "WARNING|ERROR|Traceback|Deprecat"' _ "$SHOTS/pad"
        DESC="page walk stderr has no WARNING/ERROR/Traceback/Deprecation"
        step bash -c '! grep -vE "^\[gamescope\]|Locale not supported|fallback .C. locale" "$1" | grep -E "WARNING|ERROR|Traceback|Deprecat"' _ "$SHOTS/check.err"

        # live preview: render proof needs GStreamer's gtk4paintablesink, from this host or from the installed Flatpak
        mkdir -p "$XDG_CONFIG_HOME"
        PV="$SHOTS/preview"
        PV_MODE=fallback
        PV_RUN=(python3 "$REPO/tools/previewwalk.py" "$REPO" "$PV")
        if gst-inspect-1.0 gtk4paintablesink >/dev/null 2>&1 && gst-inspect-1.0 v4l2src >/dev/null 2>&1; then
            PV_MODE=render
        elif flatpak list --user --app --columns=application 2>/dev/null | grep -qx hu.irl.cameractrls; then
            PV_MODE=render
            PV_RUN=(flatpak run --user --socket=x11 --nosocket=wayland "--filesystem=$REPO:ro" "--filesystem=$SHOTS" "--filesystem=$XDG_CONFIG_HOME" "--env=XDG_CONFIG_HOME=$XDG_CONFIG_HOME" --command=python3 hu.irl.cameractrls "$REPO/tools/previewwalk.py" "$REPO" "$PV")
        fi
        for ATTEMPT in 1 2 3; do
            for _ in $(seq 1 60); do pgrep -x gamescope-wl >/dev/null || break; sleep 3; done
            rm -f "$XDG_RUNTIME_DIR"/gamescope-0 "$XDG_RUNTIME_DIR"/gamescope-0.lock "$XDG_RUNTIME_DIR"/gamescope-0-ei*
            env -u WAYLAND_DISPLAY GDK_BACKEND=x11 timeout -s INT 90 gamescope --backend headless -W 1280 -H 960 -- "${PV_RUN[@]}" >"$PV.out" 2>"$PV.err"
            # someone else streaming makes the first start busy, give them time
            if [ "$PV_MODE" = render ] && grep -q "^MESSAGE=The camera is busy" "$PV.out"; then sleep 20; else break; fi
        done
        if [ "$PV_MODE" = render ]; then
            DESC="preview toggle renders live frames in the window ($(grep -E '^(LABEL|INTRINSIC)=' "$PV.out" | tr '\n' ' '))"
            step bash -c 'grep -qx "PANE_BEFORE=0" "$1" && grep -qx "PANE_ON=1" "$1" && grep -qx "MESSAGE=" "$1" && grep -qE "^INTRINSIC=[1-9][0-9]*x[1-9][0-9]*$" "$1" && grep -qE "^LABEL=[0-9]+x[0-9]+, [0-9]+ fps$" "$1" && awk -F= "\$1 == \"FRAMES_ADVANCE\" {exit !(\$2 >= 5)}" "$1" && grep -qx "PNG_DIFFER=1" "$1"' _ "$PV.out"
            DESC="preview samples the frame luma for the dark watchdog ($(grep -E '^LUMA=' "$PV.out"))"
            step bash -c 'grep -qE "^LUMA=[0-9]+(\.[0-9])?$" "$1"' _ "$PV.out"
            DESC="preview toggle off hides the pane, saves off and releases the camera"
            step bash -c 'grep -qx "PANE_OFF=0" "$1" && grep -qx "INI=on" "$1" && grep -qx "INI_OFF=off" "$1" && grep -qx "REOPEN_RC_AFTER_OFF=0" "$1"' _ "$PV.out"
            DESC="preview names the holder when the camera is busy and starts again on Try again"
            step bash -c 'grep -qE "^BUSY_MESSAGE=The camera is busy, .+ is using it" "$1" && grep -qx "BUSY_RETRY_BUTTON=1" "$1" && grep -qx "BUSY_PREVIEW_RUNNING=0" "$1" && grep -qx "RETRY_RUNNING=1" "$1"' _ "$PV.out"
        else
            echo "SKIP  live preview render (no gtk4paintablesink on this host and no Flatpak install), checking the fallback message instead"
            DESC="preview toggle without GStreamer shows the message and still saves its state"
            step bash -c 'grep -qx "PANE_ON=1" "$1" && grep -q "^MESSAGE=Live preview needs GStreamer" "$1" && grep -qx "INI=on" "$1" && grep -qx "INI_OFF=off" "$1"' _ "$PV.out"
        fi
        DESC="preview walk stderr has no Traceback/WARNING/ERROR/CRITICAL"
        step bash -c '! grep -vE "^\[gamescope\]|Locale not supported|fallback .C. locale|CAP_SYS_NICE|Performance will be|Tracing is enabled|^>|Wayland events" "$1" | grep -E "WARNING|ERROR|Traceback|CRITICAL"' _ "$PV.err"

        # the Check remote dialog on fake frames
        CW="$SHOTS/checkdlg"
        for _ in $(seq 1 60); do pgrep -x gamescope-wl >/dev/null || break; sleep 3; done
        rm -f "$XDG_RUNTIME_DIR"/gamescope-0 "$XDG_RUNTIME_DIR"/gamescope-0.lock "$XDG_RUNTIME_DIR"/gamescope-0-ei*
        env -u WAYLAND_DISPLAY GDK_BACKEND=x11 timeout -s INT 120 gamescope --backend headless -W 1280 -H 960 -- python3 "$REPO/tools/checkwalk.py" "$REPO" "$CW" >"$CW.out" 2>"$CW.err"
        DESC="Check remote dialog walks 22 buttons on fake frames and fills the Last remote check row"
        step bash -c 'grep -qx "DIALOG_OPEN=1" "$1" && grep -qx "DONE=1" "$1" && grep -q "^INFO_BEFORE=not run yet" "$1" && grep -qE "^INFO_AFTER=[0-9-]+ [0-9:]+: 22 of 22 buttons confirmed" "$1"' _ "$CW.out"
    fi
fi

echo
if [ "$FAILS" -eq 0 ]; then echo "check.sh: all checks passed"; else echo "check.sh: $FAILS check(s) FAILED"; fi
exit $((FAILS > 0))
