# cameractrls, EMEET C60E fork

This is a fork of Gergo Koteles's [cameractrls](https://github.com/soyersoyer/cameractrls) 0.6.10 (LGPL-3.0-or-later, the `LICENSE` file is upstream's and unchanged) tuned for one camera: the EMEET SmartCam C60E 4K Dual (USB id 328f:00e1). It is a webcam control panel for Linux: sliders for the V4L2 controls, presets, a live preview, and the extras this camera has (light colour, lens switch, zoom steps that match its IR remote). Other cameras still work as in upstream. Nothing here has been sent upstream.

Hardware: any EMEET C60E 4K Dual on a Linux box with GTK 4. Reading what the IR remote changed needs the patched uvcvideo driver (see the `uvcvideo-emeet` repo); without it the app works but shows stale values after a remote press.

## Install

    git clone <this repo> && cd cameractrls-emeet
    python3 cameractrlsgtk4.py

Needs python-gobject with GTK 4, and SDL2 plus libjpeg-turbo for the preview. `tools/check.sh` runs the checks against a real camera (it finds the C60E by its USB id; set `CAMERA=/dev/videoN` to override). `flatpak/hu.irl.cameractrls.yml` and `tools/flatpak-build.sh` build a sandboxed Flatpak.

## Specific to the author's setup

- `tools/emeet-presets.ini` holds the four presets I use (Stream, 60 fps, Quality, Natural), tuned for one room; append them to the device ini in `~/.config/hu.irl.cameractrls/` if you want them.
- `tools/flatpak-build.sh` installs the Flatpak for the current user and restarts the `cameractrlsd.service` user unit, which the app also writes itself ("Start with Systemd").
- The measured numbers in "Picture quality, measured" are from one camera in one room.

## What's different

- Controls whose min equals max (the C60E's `zoom_continuous`) are hidden everywhere.
- `cameraview.py` exits cleanly on Ctrl+C, SIGTERM and window close.
- Presets store only control values. The pixel format, resolution and fps are saved only if you turn on "Include capture format" next to the Preset row, or use `preset=save_1_fmt` on the CLI. Loading a preset whose format the camera cannot do gives one message and leaves the format alone. `cameractrlsd` never restores the format.
- "Start with Systemd" shows the real state. If `cameractrlsd.service` already exists and isn't what this app would write, the switch is on, greyed out, and the unit is never touched. When the app writes the unit itself, ExecStart is the host script path, or the `flatpak run` line inside a Flatpak.
- Exposure Time, Absolute reads like `15600 us (1/64 s)`.
- An "EMEET C60E" category on the Basic page with a Reset image button (brightness, contrast, saturation, hue, gamma, sharpness, backlight compensation and zoom back to defaults). A Zoom step menu and a Lens row sit under it (the steps come from `EMEET_C60E_ZOOM_STEPS` in `cameractrls.py`).

- Named presets are one click away: give a preset a name (right click its button) and it also shows up as a toggle button in the header bar, next to the viewer button. The one whose stored values match the camera right now is lit.
- No camera, a busy camera and an unplugged camera each get a message instead of a blank window. If the viewer cannot start because another program is streaming, the message names the process (read from `/proc`, so only processes visible to you show up) and has a Try again button.
- The Cameractrlsd section on the Advanced page shows whether the restore daemon is running and when it last applied a preset. On the host that is `systemctl --user is-active` and the journal. In the sandboxed build the daemon writes `cameractrlsd.status` (with a heartbeat) into the config dir and the app reads that.
- Every control has a tooltip, the Info block on the Capture page is a key/value grid, and slider values line up across rows.

The camera's vendor extension unit is a firmware trace mailbox, so this fork never writes to it.

## Keys

In the main window (never while you are typing in a text field):

| Key | Does |
| --- | --- |
| Alt+1 ... 5 | switch page |
| Ctrl+1 ... 4 | load preset 1 to 4 |
| Ctrl+R | reset image (the C60E Reset row) |
| Ctrl+V | open the viewer |
| F5 | refresh the camera list |
| Ctrl+Q | quit |

The same list is under Shortcuts in the hamburger menu.

In the viewer: F or double click for fullscreen, R rotate, M mirror, C colormap (Shift reverses these), H shows or hides the key hint (it shows for the first five seconds), Q or Esc closes. The window title carries the camera name, size and fps.

## Run it

    python3 cameractrlsgtk4.py                 # GTK 4 app
    python3 cameractrls.py -d /dev/video0 -l   # list controls
    python3 cameractrls.py -d /dev/video0 -c brightness=140
    python3 cameractrls.py -d /dev/video0 -c preset=save_1       # controls only
    python3 cameractrls.py -d /dev/video0 -c preset=save_1_fmt   # controls plus format
    python3 cameraview.py -d /dev/video0       # preview window

Needs python-gobject with GTK 4, and SDL2 plus libjpeg-turbo for the preview.

## Remote buttons, step values and 4K

The Basic page has a Remote section with the IR remote's buttons the host is able to do: Zoom out / Zoom in (steps of 10, hold to repeat), 1X 3X 5X 7X 11X, Switch (wide to tele and back), Focus (autofocus toggle), Reset, and Brightness, Sharpness and Color down/up in steps of 8. Flip, Mirror and the image effects happen inside the camera and the firmware gives the host no control for them, so there are no buttons for those.

The 1X..11X labels on the remote do not match real magnification, so the app starts with the values measured from the remote on 2026-10-15 (0, 100, 200, 300, 400). Change a value by long pressing the step button in the app and typing a number (0 to 400), by editing `[remote_steps]` in the device ini, or with `cameractrls.py -c ... --set-step 3x=253`. `-l` and the Step values row show each step's value and approximate magnification, and mark the ones you set.

The app cannot read what the remote's own buttons do. The zoom, steps, brightness, sharpness, colour, flip, mirror and effects from the remote all act inside the camera and show in the video, but the camera does not tell the UVC driver about them (no status interrupt, no auto-update flag), so the Linux uvcvideo driver keeps serving the value it cached from the last host write. Reading zoom after a remote press returns the old number, and the vendor query ioctl cannot read camera-terminal or processing-unit controls. That is why a step cannot be learned from the remote: the value you would read is stale.

The Zoom, Absolute slider reads like `253 (about 2.0x, Tele)`. The factor is interpolated from measurements (0 is 1.0x, 165 is 1.5x, 170 is 1.6x, 260 is 2.0x, 320 is 2.4x, 400 is 3.2x of the wide view), so it is approximate.

The Capture page has a Capture mode row: 4K 30, 1440p 30, 1080p 60, 1080p 30, 720p 60. It sets MJPG, the resolution and the fps together and hides modes the camera does not list. It applies to cameractrls' own viewer and the next app that opens the camera. OBS sets its own format while it streams, so use Lark's 4K switch for the stream. A preset saved with Include capture format keeps the mode.

After a remote press the camera sometimes runs exposure itself and reports 4078; Re-sync takes it back.

## Camera quirks

The firmware does three odd things. Remote presses change controls behind the host's back, so reads go stale on a stock kernel; the patched uvcvideo (see the `uvcvideo-emeet` repo) makes them live and the "Changed by remote" chip names what moved. The cause of most of the odd exposure behaviour is that the camera takes an `auto_exposure` mode write only while a video stream is running; without a stream the write is silently ignored, so the mode never changed and exposure stays on whatever the camera runs itself (reporting 4078). Re-sync, every preset load (including the restore daemon's) and the Fix button therefore hold a short 2-buffer capture open while they write, unless something is already streaming, then set aperture priority, wait 300 ms, set the wanted mode, read it back and retry the write up to 3 times with a 200 ms pause, and write exposure and gain inside the same stream. The picture also goes dark at times (mean luma around 16 in a lit room) while every control reads normal; while the preview is on the app samples the frame brightness every 2 s, and if it stays under 22 for 4 s after being more than twice as bright within the last minute it shows "Camera went dark" with a Fix button that redoes the mode cycle, rewrites exposure and gain, reloads the active preset and restarts the preview. If that does not take, the banner offers Reset camera (USB), also a button in the Remote category: it resets the camera on the USB bus (USBDEVFS_RESET, needs the `70-emeet-c60e.rules` udev rule from the uvcvideo-emeet repo, otherwise it says so), waits for it to come back and applies the active preset again. The viewer window has no watchdog.

## Preview

The header bar has a Live preview toggle (the monitor icon, next to the viewer button). On, the left side of the window shows what the camera sees, with the size and the measured fps under it. It resizes with the window. The state is saved per camera in the device ini (`[ui]`, `preview = on`) and it starts off.

It is a GStreamer pipeline inside the app (`v4l2src ! jpegdec ! videoconvert ! gtk4paintablesink`, in `empreview.py`) and asks the camera for the capture format it already has, so it never fights OBS over a different format. Turning it off, closing the window, switching camera or changing a format control lets go of the camera. Changing the resolution, fps, capture mode or loading a preset stops the preview first (the camera refuses a new format while something streams) and brings it back after.

If OBS, the viewer or anything else holds the camera, the pane says which program (read from `/proc`, so only processes visible to you show up) and has a Try again button. Toggling it off and on tries again too. If GStreamer or one of its plugins is missing the pane says that and the viewer button still works. The Flatpak ships all of these.

## Check your remote

`cameractrls.py -d DEVICE --remote-check` (or the Check remote button in the Remote category, which opens a dialog) walks the Dual's remote in reading order: Reset, Focus, Switch, Flip, Mirror, Zoom in, Zoom out, Sharpness -, Sharpness +, 1X, 3X, 5X, 7X, 11X, Colour -, Colour +, Brightness -, Brightness +, Effect 1 to 4. It takes a reference frame, then for each button it tells you to press it, waits 3 seconds (`--check-wait SEC`, or the spin button in the dialog), takes a frame and compares it with the one before.

The comparison is in `emcheck.py` and is plain image analysis, no libraries beyond the standard library. Frames are read as raw RGB, 320x180, through GStreamer (the same source chain as the preview) or, if that is missing, through ffmpeg. Per button it records one or more of: flipped, mirrored, zoomed in, zoomed out, brighter, darker, more colour, less colour, sharper, softer, effect (colour change), or no change. Flip and mirror are found by correlating the frame with its rotated and mirrored copies, zoom by searching centre-crop scales in both directions, brightness from mean luma, colour from saturation and hue, sharpness from edge energy. A button's HID push (what `--watch-remote` prints) counts as "camera acknowledged" when the picture test is inconclusive.

The end is a table and one plain verdict per button: works, the camera acknowledged it but the picture did not confirm it, the picture changed some other way, or no sign. The result is saved under `[remote_check]` in the device ini with a date, and the Remote category shows the last one in a Last remote check row.

What it cannot do: judge a button whose effect is not in the picture or already matches the current state (1X when you are at 1X, Reset on a clean image), so those lean on the HID push. A person moving in front of the camera, auto exposure settling or a dark or flat scene hides a change, so point the camera at something with some texture and keep still. The thresholds are tuned on synthetic frames only; they have not been checked against the real remote yet.

## Controller

Plug in an Xbox 360 style pad (any SDL2 game controller works) and press the gamepad button in the header. For the C60E it shows `Pad: Xbox 360 connected` (the state is read when the camera opens or the list is refreshed with F5). Click the pad's name in the popover and it controls the camera. A Button map button in the same popover lists every binding. From a terminal: `python3 cameraptzgame.py -d /dev/video0` runs it, `-m` prints the map, `-l` lists pads. It exits cleanly on Ctrl+C, SIGTERM and when the pad is unplugged.

| Control | Does |
| --- | --- |
| RT / LT | zoom in / out, the harder you pull the faster |
| Right stick up / down | fine zoom |
| A / B / X / Y | zoom step 1X / 3X / 5X / 11X |
| D-pad up / down | step 7X / Wide 1.5x (160) |
| D-pad left / right | previous / next preset (named ones first, only saved ones) |
| LB / RB | wide lens (0) / tele lens (170) |
| Start | Reset image |
| Back | autofocus on / off |
| Guide | next light colour (needs the Light colour control, otherwise it logs and does nothing) |
| Left stick click | open the viewer |
| Right stick click | show / hide the viewer's key hint (only for a viewer the pad opened) |
| Back + Start held 2 s | load preset 1 |

Start and Back fire when you let go, so the chord never triggers a reset or a focus toggle on the way.

The X labels are the remote's, not real magnification. The step values are the ones measured from the remote on 2026-10-15 (1X 0, 3X 100, 5X 200, 7X 300, 11X 400) unless the device ini has a `[remote_steps]` section with `1x`, `3x`, `5x`, `7x`, `11x` keys.

### Rebinding

The map lives in the device ini (`~/.config/hu.irl.cameractrls/<device>.ini`) under `[pad]`. Anything you leave out keeps its default, a typo is logged and ignored.

    [pad]
    a = lens:tele
    dpup = preset:2
    righttrigger = zoom_in
    restore_chord = back+start
    restore_hold_ms = 2000
    debounce_ms = 40
    repeat_ms = 60
    deadzone = 4000
    zoom_speed = 200
    fine_speed = 40

Buttons: `a b x y back guide start leftstick rightstick leftshoulder rightshoulder dpup dpdown dpleft dpright`. Axes: `lefttrigger righttrigger leftx lefty rightx righty`. Actions: `step:1x|3x|5x|7x|11x`, `zoom:<0-400>`, `lens:wide|tele`, `preset:1..4`, `preset_prev`, `preset_next`, `reset_image`, `focus_toggle`, `light_cycle`, `viewer`, `viewer_hint`, `none`; axes take `zoom_in`, `zoom_out`, `zoom_fine`. `zoom_speed` and `fine_speed` are zoom units per second at full pressure, `repeat_ms` is the shortest gap between zoom writes, `debounce_ms` ignores a repeat press of the same button.

## Picture quality, measured

Presets: 1 Stream (aperture-priority exposure, fixed focus 384, white balance 5500, zoom 0, sharpness 128), 2 "60fps (light)" (manual exposure 156 plus gain 50: this camera holds 60 fps only in manual mode, and in my basement that is dark without a key light), 3 Quality (aperture priority, sharpness 160, 4K30 MJPG capture saved with the format), 4 Natural (the camera's defaults with continuous focus and white balance locked at 5500). A lit-room rerun on 2026-10-15 corrected the dark-room table below: aperture priority and manual 156 both sat at luma 99 while the camera happened to be running its own exposure, and once mode writes really took effect manual 156 at gain 0 fell to luma 16. Sharpness 160 is neutral on the edge metric in light (76 vs 78), 200 doubles it (132) at the same noise and is worth an eye check before trusting, contrast 144 adds a little (82) but darkens, gain and saturation changes buy nothing in light. All four are in `tools/emeet-presets.ini`; append it to the device ini in `~/.config/hu.irl.cameractrls/` (host) or `~/.var/app/hu.irl.cameractrls/config/hu.irl.cameractrls/` (Flatpak). Loading a preset applies the auto switches (exposure, white balance, focus) first, inside a short stream, so a preset with a manual focus value loads clean while continuous focus is on.

`tools/measure.py IMAGE...` prints sharpness (variance of the Laplacian on the centre crop, higher is sharper), noise (std of the high-pass residual in the flattest tiles, lower is cleaner), the grey-world R/G and B/G, a rough colour temperature and mean luma. It only reads image files. Every row below is the median of three 1.5 s ffmpeg grabs at 1920x1080 (the 4K row is a 3840x2160 grab downscaled with Lanczos) with one setting changed from the Stream baseline.

### Measured on 2026-10-15, dark basement

The lights were off for this run, not on: mean luma is about 15 of 255 and the lens sees the far wall and a clock, no person. So the numbers show what the camera does in a dark room, and the lit-room comparison still has to be done. Repeat spread of the baseline (four runs) is sharp 2.20 to 2.28, noise 0.30 to 0.31, so anything inside about 5 percent is noise.

| setting | sharp (centre) | sharp (full) | noise | luma | R/G | B/G |
|---|---|---|---|---|---|---|
| baseline: manual exp 156, gain 0, sharp 128, contrast 128, sat 128, WB 5500 | 2.28 | 3.55 | 0.313 | 15.6 | 2.47 | 1.45 |
| exposure 200 | 2.29 | 3.57 | 0.309 | 15.6 | 2.49 | 1.48 |
| exposure 250 | 2.28 | 3.62 | 0.306 | 15.6 | 2.46 | 1.45 |
| aperture-priority auto | 2.26 | 3.63 | 0.306 | 15.6 | 2.48 | 1.47 |
| gain 10 | 2.28 | 3.65 | 0.309 | 15.7 | 2.47 | 1.47 |
| gain 20 | 2.21 | 3.64 | 0.302 | 15.6 | 2.47 | 1.46 |
| sharpness 100 | 2.17 | 3.55 | 0.307 | 15.6 | 2.46 | 1.48 |
| sharpness 160 | 2.39 | 3.89 | 0.303 | 15.6 | 2.40 | 1.45 |
| contrast 112 | 2.00 | 3.39 | 0.282 | 23.1 | 1.78 | 1.26 |
| contrast 144 | 2.24 | 3.81 | 0.284 | 8.2 | 6.80 | 2.83 |
| saturation 112 | 2.29 | 3.67 | 0.306 | 15.7 | 2.20 | 1.35 |
| saturation 144 | 2.21 | 3.72 | 0.306 | 15.6 | 2.69 | 1.50 |
| backlight compensation 2 | 2.28 | 3.67 | 0.308 | 15.7 | 2.46 | 1.47 |
| WB 5000 | 2.15 | 3.57 | 0.290 | 15.4 | 2.40 | 1.58 |
| WB 6000 | 2.43 | 3.84 | 0.321 | 15.9 | 2.60 | 1.45 |
| auto WB | 1.26 | 2.60 | 0.180 | 14.0 | 1.62 | 1.65 |
| 1080p30 instead of 1080p60 | 4.12 | 6.86 | 0.401 | 17.7 | 2.64 | 1.50 |
| 4K30 downscaled to 1080p | 5.02 | 8.60 | 0.423 | 18.8 | 2.39 | 1.39 |

What it says:

- Exposure (even 5 to 4000), gain, brightness and aperture-priority change nothing in a dark room: luma stays at 15.6. Nothing to tune there from this run, Stream is unchanged.
- Sharpness 160 is the only setting that moves sharpness up without costing noise (+5 percent centre, +7 percent full frame, close to the repeat spread), so Quality takes it. 100 goes the other way.
- Contrast moves luma and the colour cast a lot (112 brightens the shadows, 144 crushes them), which in a dark room is a brightness change, not detail. Saturation moves only the colour ratios. Both stay at 128.
- Auto white balance flips the cast (R/G 1.62, B/G 1.65) and cuts the measured sharpness in half, so white balance stays locked. A grey-world estimate in a room with no grey reference cannot pick between 5000, 5500 and 6000; 5500 stays.
- 4K30 downscaled is clearly the sharpest (2.2 times the 1080p60 centre sharpness) at the price of about 35 percent more noise, and 1080p30 lands in between. Part of that is more light per frame at 30 fps (luma 17.7 to 18.8 against 15.6), so how much is real resolution is not separated here. That is why Quality saves the 4K30 capture mode with the format.

## tools/check.sh

Run `tools/check.sh` with the camera plugged in. It:

1. parses every `.py` file to catch syntax errors,
2. runs the CLI `-L` and `-l` and checks the listing (no dead controls, the EMEET category is there, no warnings),
3. saves and loads a preset in a temp `XDG_CONFIG_HOME`, checks the format is stored only when asked, and puts brightness back,
4. checks the Remote category, the step value (writes `[remote_steps]` in the temp ini), the zoom factor text and the capture modes, and puts zoom and format back (the capture mode checks skip while something streams from the camera),
5. checks that presets 3 and 4 ship with names, load without a warning (controls only, then the live controls are put back) and that `tools/measure.py` computes its metrics on synthetic images (`tools/test_measure.py`),
6. runs the pad mapper tests (`tools/test_pad.py`, no pad or camera needed), lists the pad, prints the map and checks the pad process exits 0 on SIGTERM,
7. runs `tools/test_remote_check.py`: the picture classifier on synthetic frames (flip, mirror, zoom, brightness, saturation, sharpness, effects, with and without noise, flat frames) and `--remote-check` on fake frames (`CAMERACTRLS_FAKE_FRAMES=<dir of .ppm>`),
8. walks the five GTK pages under headless gamescope (`tools/walk2.sh`, `tools/pagewalk.py`) and fails on any WARNING, ERROR or Traceback,
9. opens the Check remote dialog on fake frames (`tools/checkwalk.py`), then toggles the live preview in the headless window (`tools/previewwalk.py`): frames must advance and differ, a second program holding the camera must be named, and the camera must be free again after. That needs gtk4paintablesink, from the host or from the installed Flatpak; without either it checks the "needs GStreamer" message instead.

It exits non-zero if anything fails. `SKIP_UI=1` skips the page walk, `CAMERA=` picks another device, `SHOTS=` keeps the screenshots. Your real preset directory is never touched.
