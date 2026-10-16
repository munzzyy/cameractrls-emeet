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
pages = ['0', '1', '2', '3', '4']
def step(i):
    if i >= len(pages):
        app.window.close(); return False
    st = app.window.stack
    if st and st.get_child_by_name(pages[i]):
        st.set_visible_child_full(pages[i], m.Gtk.StackTransitionType.NONE)
    GLib.timeout_add(1800, lambda: (shot(f'page{i}'), GLib.timeout_add(300, step, i + 1)) and False)
    return False
GLib.timeout_add(7000, step, 0)
app.run()
app.kill_child_processes()
