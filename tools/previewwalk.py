import os, sys, subprocess, shutil, time
APPDIR, OUT = sys.argv[1], sys.argv[2]
sys.path.insert(0, APPDIR); os.chdir(APPDIR)
sys.argv = [os.path.join(APPDIR, 'cameractrlsgtk4.py')]
import cameractrlsgtk4 as m
import emcheck
from gi.repository import GLib, Gtk, Gdk, Graphene

app = m.CameraCtrlsApp()
env = {**os.environ, 'GAMESCOPE_WAYLAND_DISPLAY': os.environ.get('LIBEI_SOCKET', 'gamescope-0-ei')[:-3], 'XDG_RUNTIME_DIR': os.environ.get('XDG_RUNTIME_DIR', '/run/user/1000')}
HAVE_SHOT = shutil.which('gamescopectl') is not None
out = {}

def shot(name):
    if HAVE_SHOT:
        subprocess.run(['gamescopectl', 'screenshot', f'{OUT}-{name}.png'], env=env, capture_output=True)
        return
    win = app.window
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable(widget=win).snapshot(snap, win.get_width(), win.get_height())
    node = snap.to_node()
    if node is not None:
        win.get_native().get_renderer().render_texture(node, None).save_to_png(f'{OUT}-{name}.png')

def paint_png(win):
    p = win.preview_picture.get_paintable()
    if p is None or p.get_intrinsic_width() <= 0:
        return None
    snap = Gtk.Snapshot()
    p.snapshot(snap, 320, 180)
    node = snap.to_node()
    if node is None:
        return None
    return win.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, 320, 180)).save_to_png_bytes().get_data()

def run():
    win = app.window
    tog = win.preview_toggle
    out['TOGGLE_VISIBLE'] = int(tog.get_visible())
    out['PANE_BEFORE'] = int(win.preview_pane.get_visible())
    tog.set_active(True)
    GLib.timeout_add(2500, check_on)
    return False

def check_on():
    win = app.window
    out['PANE_ON'] = int(win.preview_pane.get_visible())
    out['MESSAGE'] = win.preview_message.get_label() if win.preview_message.get_visible() else ''
    out['LABEL'] = win.preview_label.get_label()
    out['INI'] = emcheck.ini_get(win.ini_path(), 'ui', 'preview')
    p = win.preview_picture.get_paintable()
    out['INTRINSIC'] = f'{p.get_intrinsic_width()}x{p.get_intrinsic_height()}' if p else 'none'
    out['PREVIEW_RUNNING'] = int(win.preview is not None)
    out['FRAMES_1'] = win.preview.frames if win.preview else 0
    out['PNG_1'] = paint_png(win) if win.preview else None
    shot('on1')
    GLib.timeout_add(1000, check_later)
    return False

def check_later():
    win = app.window
    out['FRAMES_2'] = win.preview.frames if win.preview else 0
    png2 = paint_png(win) if win.preview else None
    out['LUMA'] = '' if not win.preview or win.preview.luma is None else '%.1f' % win.preview.luma
    out['FRAMES_ADVANCE'] = out['FRAMES_2'] - out['FRAMES_1']
    out['PNG_DIFFER'] = int(bool(out['PNG_1']) and bool(png2) and out['PNG_1'] != png2)
    shot('on2')
    win.preview_toggle.set_active(False)
    GLib.timeout_add(800, check_off)
    return False

def check_off():
    win = app.window
    out['PANE_OFF'] = int(win.preview_pane.get_visible())
    out['INI_OFF'] = emcheck.ini_get(win.ini_path(), 'ui', 'preview')
    out['REOPEN_RC_AFTER_OFF'] = subprocess.run(['ffmpeg', '-v', 'quiet', '-f', 'v4l2', '-i', win.device.path, '-frames:v', '1', '-f', 'null', '-'], timeout=30).returncode
    # someone else streams: the message must name them
    cam = subprocess.Popen(['ffmpeg', '-v', 'quiet', '-f', 'v4l2', '-i', win.device.path, '-f', 'null', '-'])
    out['_cam'] = cam
    time.sleep(2)
    win.preview_toggle.set_active(True)
    GLib.timeout_add(2500, check_busy)
    return False

def check_busy():
    win = app.window
    cam = out.pop('_cam')
    out['BUSY_MESSAGE'] = win.preview_message.get_label() if win.preview_message.get_visible() else ''
    out['BUSY_RETRY_BUTTON'] = int(win.preview_retry.get_visible())
    out['BUSY_PREVIEW_RUNNING'] = int(win.preview is not None)
    shot('busy')
    cam.terminate(); cam.wait()
    time.sleep(1)
    win.preview_retry.emit('clicked')
    GLib.timeout_add(2500, check_retry)
    return False

def check_retry():
    win = app.window
    out['RETRY_RUNNING'] = int(win.preview is not None)
    out['RETRY_MESSAGE'] = win.preview_message.get_label() if win.preview_message.get_visible() else ''
    win.preview_toggle.set_active(False)
    win.close()
    return False

GLib.timeout_add(7000, run)
app.run()
app.kill_child_processes()
for k, v in out.items():
    if k.startswith('PNG_') and k != 'PNG_DIFFER':
        continue
    print(f'{k}={v}')
