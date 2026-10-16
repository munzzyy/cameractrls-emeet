import os, sys, subprocess, tempfile, pathlib
APPDIR, OUT = sys.argv[1], sys.argv[2]
sys.path.insert(0, APPDIR); sys.path.insert(1, os.path.join(APPDIR, 'tools')); os.chdir(APPDIR)
sys.argv = [os.path.join(APPDIR, 'cameractrlsgtk4.py')]
import emcheck, test_remote_check as t
frames = pathlib.Path(tempfile.mkdtemp(prefix='frames'))
for n, f in enumerate(t.sequence(t.scene())):
    (frames / f'{n:03d}.ppm').write_bytes(emcheck.frame_to_ppm(f))
os.environ[emcheck.FAKE_FRAMES_ENV] = str(frames)
import cameractrlsgtk4 as m
from gi.repository import GLib, Gtk
env = {**os.environ, 'GAMESCOPE_WAYLAND_DISPLAY': os.environ.get('LIBEI_SOCKET', 'gamescope-0-ei')[:-3], 'XDG_RUNTIME_DIR': '/run/user/1000'}
app = m.CameraCtrlsApp()
out = {}

def shot(name):
    subprocess.run(['gamescopectl', 'screenshot', f'{OUT}-{name}.png'], env=env, capture_output=True)

def find(widget, kind, label):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind) and (label is None or getattr(child, 'get_label', lambda: None)() == label):
            return child
        hit = find(child, kind, label)
        if hit is not None:
            return hit
        child = child.get_next_sibling()
    return None

def top():
    return next(w for w in Gtk.Window.list_toplevels() if w.get_title() == 'Check remote')

def open_dialog():
    win = app.window
    info = m.find_by_text_id(win.camera.get_ctrls(), 'emeet_remote_check_info')
    out['INFO_BEFORE'] = info.value
    win.update_ctrl(m.find_by_text_id(win.camera.get_ctrls(), 'emeet_remote_check'), 'run')
    GLib.timeout_add(800, press_start)
    return False

def press_start():
    dlg = top()
    shot('dialog0')
    out['DIALOG_OPEN'] = 1
    find(dlg, Gtk.SpinButton, None).set_value(0)
    find(dlg, Gtk.Button, 'Start').emit('clicked')
    GLib.timeout_add(2000, mid)
    return False

def mid():
    shot('dialog1')
    GLib.timeout_add(1000, wait_done)
    return False

def wait_done():
    dlg = top()
    if find(dlg, Gtk.Button, 'Run again') is None:
        return True
    out['DONE'] = 1
    shot('dialog2')
    info = m.find_by_text_id(app.window.camera.get_ctrls(), 'emeet_remote_check_info')
    out['INFO_AFTER'] = info.value
    GLib.timeout_add(1500, lambda: (dlg.close(), GLib.timeout_add(800, app.window.close), False)[2])
    return False

GLib.timeout_add(7000, open_dialog)
app.run()
app.kill_child_processes()
for k, v in out.items():
    print(f'{k}={v}')
