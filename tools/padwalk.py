import os, sys, subprocess
APPDIR, PREFIX = sys.argv[1], sys.argv[2]
sys.path.insert(0, APPDIR); os.chdir(APPDIR)
sys.argv = [os.path.join(APPDIR, 'cameractrlsgtk4.py')]
import cameractrlsgtk4 as m
from gi.repository import GLib
env = {**os.environ, 'GAMESCOPE_WAYLAND_DISPLAY': os.environ.get('LIBEI_SOCKET', 'gamescope-0-ei')[:-3], 'XDG_RUNTIME_DIR': '/run/user/1000'}
def shot(name):
    subprocess.run(['gamescopectl', 'screenshot', f'{PREFIX}-{name}.png'], env=env, capture_output=True)
app = m.CameraCtrlsApp()
app.set_application_id('hu.irl.cameractrls.padwalk')
def later(ms, fn):
    GLib.timeout_add(ms, lambda: fn() and False)
def finish():
    app.window.close()
def map_shot():
    shot('map')
    later(500, finish)
def open_map():
    print(f'MAPBUTTON={int(app.window.pad_map_button.get_visible())}')
    app.window.show_pad_map()
    later(1500, map_shot)
def header():
    w = app.window
    print(f'PADLABEL={w.pad_label.get_label() if w.pad_label.get_visible() else ""}')
    shot('header')
    later(500, open_map)
later(7000, header)
app.run()
app.kill_child_processes()
