#!/usr/bin/env python3
"""Game controller profile for the EMEET C60E. No SDL in here: the mapper is fed button and axis state."""

import configparser, logging, os, signal, subprocess, sys, time

C60E_USB_ID = '328f:00e1'

TELE_FROM = 170
FACTORY_STEPS = {'1x': 0, '3x': 100, '5x': 200, '7x': 300, '11x': 400}
WIDE_1_5X = 160
PRESET_NUMS = ('1', '2', '3', '4')

BUTTONS = ['a', 'b', 'x', 'y', 'back', 'guide', 'start', 'leftstick', 'rightstick',
           'leftshoulder', 'rightshoulder', 'dpup', 'dpdown', 'dpleft', 'dpright']
AXES = ['lefttrigger', 'righttrigger', 'leftx', 'lefty', 'rightx', 'righty']
CHORD_KEYS = ('restore_chord',)

DEFAULT_PAD = {
    'a': 'step:1x',
    'b': 'step:3x',
    'x': 'step:5x',
    'y': 'step:11x',
    'dpup': 'step:7x',
    'dpdown': 'zoom:%d' % WIDE_1_5X,
    'dpleft': 'preset_prev',
    'dpright': 'preset_next',
    'leftshoulder': 'lens:wide',
    'rightshoulder': 'lens:tele',
    'start': 'reset_image',
    'back': 'focus_toggle',
    'guide': 'light_cycle',
    'leftstick': 'viewer',
    'rightstick': 'viewer_hint',
    'righttrigger': 'zoom_in',
    'lefttrigger': 'zoom_out',
    'righty': 'zoom_fine',
}

DEFAULT_SETTINGS = {
    'restore_chord': 'back+start',
    'restore_hold_ms': 2000,
    'debounce_ms': 40,
    'repeat_ms': 60,
    'deadzone': 4000,
    'zoom_speed': 200,
    'fine_speed': 40,
}

ACTION_TEXT = {
    'zoom_in': 'Zoom in (harder = faster)',
    'zoom_out': 'Zoom out (harder = faster)',
    'zoom_fine': 'Fine zoom (up = in, down = out)',
    'lens:wide': 'Wide lens',
    'lens:tele': 'Tele lens',
    'reset_image': 'Reset image',
    'focus_toggle': 'Autofocus on/off',
    'light_cycle': 'Next light colour',
    'viewer': 'Open the viewer',
    'viewer_hint': 'Show/hide the viewer key hint',
    'preset_prev': 'Previous preset',
    'preset_next': 'Next preset',
    'none': '-',
}

BUTTON_LABEL = {
    'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y', 'back': 'Back', 'guide': 'Guide', 'start': 'Start',
    'leftstick': 'Left stick click', 'rightstick': 'Right stick click',
    'leftshoulder': 'LB', 'rightshoulder': 'RB',
    'dpup': 'D-pad up', 'dpdown': 'D-pad down', 'dpleft': 'D-pad left', 'dpright': 'D-pad right',
    'lefttrigger': 'LT', 'righttrigger': 'RT', 'righty': 'Right stick up/down',
}


def pad_label(name):
    if 'box' in name.lower():
        return 'Xbox 360'
    return name


def describe_action(action):
    if action in ACTION_TEXT:
        return ACTION_TEXT[action]
    kind, _, arg = action.partition(':')
    if kind == 'step':
        return f'Zoom step {arg.upper()}'
    if kind == 'zoom':
        return 'Wide 1.5x' if arg == str(WIDE_1_5X) else f'Zoom to {arg}'
    if kind == 'preset':
        return f'Load preset {arg}'
    return action


def valid_action(action):
    if action in ACTION_TEXT:
        return True
    kind, _, arg = action.partition(':')
    if kind == 'step':
        return arg in FACTORY_STEPS
    if kind == 'zoom':
        return arg.isdigit()
    if kind == 'preset':
        return arg in PRESET_NUMS
    return False


def read_ini(path):
    config = configparser.ConfigParser(interpolation=None)
    if path:
        try:
            config.read(path, encoding='utf-8')
        except (configparser.Error, OSError) as e:
            logging.warning(f'cameraptzc60e: can\'t read {path}: {e}')
    return config


def load_profile(config):
    """(bindings, settings, steps) from a ConfigParser: [pad] overrides the defaults, [remote_steps] the step values."""
    bindings = dict(DEFAULT_PAD)
    settings = dict(DEFAULT_SETTINGS)
    steps = dict(FACTORY_STEPS)
    if config.has_section('pad'):
        for key, value in config['pad'].items():
            value = value.strip()
            if key in settings:
                if key in CHORD_KEYS:
                    settings[key] = value.lower()
                    continue
                try:
                    settings[key] = int(value)
                except ValueError:
                    logging.warning(f'cameraptzc60e: [pad] {key} = {value!r} is not a number, keeping {settings[key]}')
            elif key in BUTTONS or key in AXES:
                if valid_action(value):
                    bindings[key] = value
                else:
                    logging.warning(f'cameraptzc60e: [pad] {key} = {value!r} is not an action, keeping {bindings.get(key)}')
            else:
                logging.warning(f'cameraptzc60e: [pad] unknown key {key!r}')
    if config.has_section('remote_steps'):
        for key, value in config['remote_steps'].items():
            if key in steps:
                try:
                    steps[key] = max(0, int(value))
                except ValueError:
                    logging.warning(f'cameraptzc60e: [remote_steps] {key} = {value!r} is not a number')
    return bindings, settings, steps


def map_rows(bindings, settings, steps):
    """(control, what it does) rows, in a stable reading order."""
    order = ['righttrigger', 'lefttrigger', 'righty', 'a', 'b', 'x', 'y', 'dpup', 'dpdown', 'dpleft', 'dpright',
             'leftshoulder', 'rightshoulder', 'start', 'back', 'guide', 'leftstick', 'rightstick']
    rows = []
    for key in order:
        action = bindings.get(key, 'none')
        text = describe_action(action)
        kind, _, arg = action.partition(':')
        if kind == 'step':
            text += f' ({steps[arg]})'
        rows.append((BUTTON_LABEL[key], text))
    chord = settings['restore_chord']
    if chord and chord != 'none':
        rows.append((' + '.join(BUTTON_LABEL.get(c, c) for c in chord.split('+')),
                     f'Hold {settings["restore_hold_ms"] / 1000:g} s: restore preset 1'))
    return rows


def format_map(bindings, settings, steps):
    rows = map_rows(bindings, settings, steps)
    width = max(len(r[0]) for r in rows)
    return '\n'.join(f'  {control:<{width}}  {what}' for control, what in rows)


def preset_order(config):
    """Preset numbers that are saved in the ini, named ones first."""
    saved = [n for n in PRESET_NUMS if config.has_section(f'preset_{n}')]
    named = [n for n in saved if config.has_section('preset_names') and config['preset_names'].get(n)]
    return named + [n for n in saved if n not in named]


class C60EPad:
    """Turns pad state into camera control writes. Feed update() on every poll."""

    def __init__(self, cam, config, load_preset, spawn_viewer=None, signal_viewer=None, out=print):
        self.cam = cam
        self.config = config
        self.bindings, self.settings, self.steps = load_profile(config)
        self.load_preset = load_preset
        self.spawn_viewer = spawn_viewer
        self.signal_viewer = signal_viewer
        self.out = out
        self.preset_idx = None
        self.down = {}
        self.last_press = {}
        self.pending = {}
        self.chord = tuple(c for c in self.settings['restore_chord'].split('+') if c in BUTTONS)
        if len(self.chord) < 2:
            self.chord = ()
        self.chord_since = None
        self.chord_fired = False
        self.chord_engaged = False
        self.zoom_f = None
        self.zoom_active = False
        self.last_zoom_write = None
        self.last_t = None

    def log(self, msg):
        self.out(msg)

    def write(self, params):
        errs = []
        self.cam.setup_ctrls(params, errs)
        for e in errs:
            self.log(f'warning: {e}')
        return not errs

    def zoom_ctrl(self):
        return self.cam.get_ctrl_by_text_id('zoom_absolute')

    def set_zoom(self, value):
        ctrl = self.zoom_ctrl()
        if ctrl is None:
            self.log('no zoom_absolute control')
            return
        value = int(min(max(value, ctrl.min), ctrl.max))
        if value != ctrl.value:
            self.write({'zoom_absolute': value})
        self.zoom_f = float(value)

    def update(self, now, buttons, axes):
        """now in seconds (monotonic), buttons = set of pressed names, axes = {name: -32768..32767}."""
        dt = 0.0 if self.last_t is None else min(now - self.last_t, 0.25)
        self.last_t = now
        pressed = set(buttons)
        for name in BUTTONS:
            is_down = name in pressed
            was_down = self.down.get(name, False)
            if is_down == was_down:
                continue
            self.down[name] = is_down
            if is_down:
                self.on_press(name, now)
            else:
                self.on_release(name)
        self.update_chord(now, pressed)
        self.update_zoom(now, dt, axes)

    def in_chord(self, name):
        return name in self.chord

    def update_chord(self, now, pressed):
        if not self.chord:
            return
        if all(c in pressed for c in self.chord):
            if self.chord_since is None:
                self.chord_since = now
                self.chord_engaged = True
            elif not self.chord_fired and now - self.chord_since >= self.settings['restore_hold_ms'] / 1000:
                self.chord_fired = True
                self.log('restore preset 1')
                self.run_preset('1')
        elif not any(c in pressed for c in self.chord):
            self.chord_since = None
            self.chord_fired = False
            self.chord_engaged = False

    def on_press(self, name, now):
        last = self.last_press.get(name)
        if last is not None and now - last < self.settings['debounce_ms'] / 1000:
            self.pending.pop(name, None)
            return
        self.last_press[name] = now
        if self.in_chord(name):
            self.pending[name] = True
            return
        self.run(self.bindings.get(name, 'none'))

    def on_release(self, name):
        if self.pending.pop(name, None) and not self.chord_engaged:
            self.run(self.bindings.get(name, 'none'))

    def update_zoom(self, now, dt, axes):
        dz = self.settings['deadzone']

        def press(axis):
            v = axes.get(axis, 0)
            return 0.0 if v < dz else min(v, 32767) / 32767

        def stick(axis):
            v = axes.get(axis, 0)
            return 0.0 if -dz < v < dz else max(-1.0, min(1.0, v / 32767))

        rate = 0.0
        for axis in ('righttrigger', 'lefttrigger'):
            action = self.bindings.get(axis, 'none')
            amount = press(axis)
            if action == 'zoom_in':
                rate += amount * self.settings['zoom_speed']
            elif action == 'zoom_out':
                rate -= amount * self.settings['zoom_speed']
        for axis in ('righty', 'lefty', 'rightx', 'leftx'):
            if self.bindings.get(axis) == 'zoom_fine':
                rate -= stick(axis) * self.settings['fine_speed']
        ctrl = self.zoom_ctrl()
        if rate == 0.0 or ctrl is None:
            self.zoom_active = False
            return
        if not self.zoom_active or self.zoom_f is None:
            self.zoom_f = float(ctrl.value)
            self.zoom_active = True
            dt = 0.0
        self.zoom_f = min(max(self.zoom_f + rate * dt, ctrl.min), ctrl.max)
        target = int(round(self.zoom_f))
        interval = self.settings['repeat_ms'] / 1000
        if target != ctrl.value and (self.last_zoom_write is None or now - self.last_zoom_write >= interval):
            self.write({'zoom_absolute': target})
            self.last_zoom_write = now

    def run_preset(self, num):
        if not self.config.has_section(f'preset_{num}'):
            self.log(f'preset {num} is not saved')
            return
        errs = []
        self.load_preset(num, errs)
        for e in errs:
            self.log(f'warning: {e}')
        self.log(f'preset {num}')

    def run(self, action):
        kind, _, arg = action.partition(':')
        if action == 'none':
            return
        if kind == 'step':
            self.set_zoom(self.steps[arg])
            self.log(f'zoom step {arg.upper()}')
        elif kind == 'zoom':
            self.set_zoom(int(arg))
            self.log(f'zoom {arg}')
        elif kind == 'lens':
            self.set_zoom(0 if arg == 'wide' else TELE_FROM)
            self.log(f'lens {arg}')
        elif kind == 'preset':
            self.run_preset(arg)
        elif action in ('preset_prev', 'preset_next'):
            self.cycle_preset(1 if action == 'preset_next' else -1)
        elif action == 'reset_image':
            if self.cam.get_ctrl_by_text_id('emeet_c60e_reset_image') is None:
                self.log('no Reset image control on this camera')
            else:
                self.write({'emeet_c60e_reset_image': 'reset'})
                self.zoom_f = None
                self.log('reset image')
        elif action == 'focus_toggle':
            self.toggle_focus()
        elif action == 'light_cycle':
            self.cycle_light()
        elif action == 'viewer':
            if self.spawn_viewer:
                self.spawn_viewer()
        elif action == 'viewer_hint':
            if self.signal_viewer and self.signal_viewer():
                self.log('viewer hint toggled')
            else:
                self.log('no viewer open from this pad, press the left stick first')
        else:
            self.log(f'unknown action {action!r}')

    def cycle_preset(self, direction):
        order = preset_order(self.config)
        if not order:
            self.log('no presets saved')
            return
        if self.preset_idx is None or self.preset_idx >= len(order):
            self.preset_idx = 0 if direction > 0 else len(order) - 1
        else:
            self.preset_idx = (self.preset_idx + direction) % len(order)
        self.run_preset(order[self.preset_idx])

    def toggle_focus(self):
        ctrl = self.cam.get_ctrl_by_text_id('focus_automatic_continuous')
        if ctrl is None or ctrl.inactive:
            self.log('no autofocus control')
            return
        new = 0 if ctrl.value else 1
        self.write({'focus_automatic_continuous': new})
        self.log(f'autofocus {"on" if new else "off"}')

    def cycle_light(self):
        ctrl = self.cam.get_ctrl_by_text_id('emeet_c60e_light')
        if ctrl is None or not getattr(ctrl, 'menu', None):
            self.log('no light colour control on this build')
            return
        values = [m.value for m in ctrl.menu]
        idx = values.index(ctrl.value) + 1 if ctrl.value in values else 0
        new = values[idx % len(values)]
        self.write({'emeet_c60e_light': new})
        self.log(f'light {new}')


class ViewerLauncher:
    def __init__(self, device, script_dir=None):
        self.device = device
        self.script_dir = script_dir or os.path.dirname(os.path.abspath(__file__))
        self.proc = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def spawn(self):
        if self.running():
            print('viewer already open')
            return
        self.proc = subprocess.Popen([os.path.join(self.script_dir, 'cameraview.py'), '-d', self.device])
        print('viewer opened')

    def toggle_hint(self):
        if not self.running():
            return False
        self.proc.send_signal(signal.SIGUSR1)
        return True

    def close(self):
        if self.running():
            self.proc.terminate()
