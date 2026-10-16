#!/usr/bin/env python3
"""Drives the C60E pad mapper with fake pad state and a fake camera. No SDL, no device."""

import configparser, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import cameraptzc60e as m


class Ctrl:
    def __init__(self, value, lo=0, hi=1, menu=None):
        self.value, self.min, self.max, self.menu, self.inactive = value, lo, hi, menu, False


class Item:
    def __init__(self, value):
        self.value = value


class FakeCam:
    def __init__(self, light=False):
        self.writes = []
        self.ctrls = {
            'zoom_absolute': Ctrl(0, 0, 400),
            'focus_automatic_continuous': Ctrl(1),
            'emeet_c60e_reset_image': Ctrl(None),
        }
        if light:
            self.ctrls['emeet_c60e_light'] = Ctrl('ff0000', menu=[Item('ff0000'), Item('00ff00'), Item('0000ff')])

    def get_ctrl_by_text_id(self, text_id):
        return self.ctrls.get(text_id)

    def setup_ctrls(self, params, errs):
        for k, v in params.items():
            self.writes.append((k, v))
            if k in self.ctrls and k != 'emeet_c60e_reset_image':
                self.ctrls[k].value = v


def make(ini='', light=False, zoom=0):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(ini)
    cam = FakeCam(light)
    cam.ctrls['zoom_absolute'].value = zoom
    log, presets = [], []
    pad = m.C60EPad(cam, config, lambda n, errs: presets.append(n), out=log.append)
    pad.sent_signal = []
    return pad, cam, log, presets


def tap(pad, t, name, hold=0.1):
    pad.update(t, {name}, {})
    pad.update(t + hold, set(), {})
    return t + hold + 0.1


class Buttons(unittest.TestCase):
    def test_zoom_steps_default(self):
        for name, want in (('a', 0), ('b', 100), ('x', 200), ('y', 400), ('dpup', 300), ('dpdown', 160)):
            pad, cam, *_ = make(zoom=33)
            tap(pad, 1.0, name)
            self.assertEqual(cam.ctrls['zoom_absolute'].value, want, name)

    def test_remote_steps_override(self):
        pad, cam, *_ = make('[remote_steps]\n3x = 123\n11x = 390\n')
        tap(pad, 1.0, 'b')
        self.assertEqual(cam.ctrls['zoom_absolute'].value, 123)
        tap(pad, 2.0, 'y')
        self.assertEqual(cam.ctrls['zoom_absolute'].value, 390)

    def test_lens(self):
        pad, cam, *_ = make(zoom=300)
        tap(pad, 1.0, 'leftshoulder')
        self.assertEqual(cam.ctrls['zoom_absolute'].value, 0)
        tap(pad, 2.0, 'rightshoulder')
        self.assertEqual(cam.ctrls['zoom_absolute'].value, 170)

    def test_reset_and_focus(self):
        pad, cam, *_ = make()
        t = tap(pad, 1.0, 'start')
        self.assertIn(('emeet_c60e_reset_image', 'reset'), cam.writes)
        t = tap(pad, t, 'back')
        self.assertEqual(cam.ctrls['focus_automatic_continuous'].value, 0)
        tap(pad, t, 'back')
        self.assertEqual(cam.ctrls['focus_automatic_continuous'].value, 1)

    def test_guide_without_light_is_a_logged_noop(self):
        pad, cam, log, _ = make()
        tap(pad, 1.0, 'guide')
        self.assertEqual(cam.writes, [])
        self.assertTrue(any('light' in l for l in log))

    def test_guide_cycles_light(self):
        pad, cam, *_ = make(light=True)
        t = 1.0
        for _ in range(4):
            t = tap(pad, t, 'guide')
        self.assertEqual([v for k, v in cam.writes if k == 'emeet_c60e_light'], ['00ff00', '0000ff', 'ff0000', '00ff00'])

    def test_debounce(self):
        pad, cam, *_ = make(zoom=5)
        pad.update(1.0, {'back'}, {}); pad.update(1.01, set(), {})
        pad.update(1.02, {'back'}, {}); pad.update(1.03, set(), {})
        self.assertEqual(cam.ctrls['focus_automatic_continuous'].value, 0)
        pad.update(1.5, {'back'}, {}); pad.update(1.55, set(), {})
        self.assertEqual(cam.ctrls['focus_automatic_continuous'].value, 1)

    def test_rebind_and_bad_binding(self):
        pad, cam, log, _ = make('[pad]\na = lens:tele\nb = fly_to_moon\ndebounce_ms = 10\n')
        tap(pad, 1.0, 'a')
        self.assertEqual(cam.ctrls['zoom_absolute'].value, 170)
        tap(pad, 2.0, 'b')
        self.assertEqual(cam.ctrls['zoom_absolute'].value, 100)
        self.assertEqual(pad.settings['debounce_ms'], 10)

    def test_viewer_and_hint(self):
        calls = []
        pad, *_ = make()
        pad.spawn_viewer = lambda: calls.append('open')
        pad.signal_viewer = lambda: calls.append('hint') or True
        t = tap(pad, 1.0, 'leftstick')
        tap(pad, t, 'rightstick')
        self.assertEqual(calls, ['open', 'hint'])

    def test_hint_without_viewer_logs(self):
        pad, cam, log, _ = make()
        pad.signal_viewer = lambda: False
        tap(pad, 1.0, 'rightstick')
        self.assertTrue(any('no viewer' in l for l in log))


class Triggers(unittest.TestCase):
    def run_axes(self, pad, axes, seconds, start=1.0, step=0.05):
        t = start
        while t < start + seconds:
            pad.update(t, set(), axes)
            t += step
        return t

    def test_right_trigger_zooms_in_by_pressure(self):
        full, cam_full, *_ = make()
        half, cam_half, *_ = make()
        self.run_axes(full, {'righttrigger': 32767}, 0.5)
        self.run_axes(half, {'righttrigger': 16384}, 0.5)
        f, h = cam_full.ctrls['zoom_absolute'].value, cam_half.ctrls['zoom_absolute'].value
        self.assertTrue(80 <= f <= 110, f)
        self.assertTrue(30 <= h <= 60, h)

    def test_left_trigger_zooms_out_and_clamps(self):
        pad, cam, *_ = make(zoom=30)
        self.run_axes(pad, {'lefttrigger': 32767}, 2.0)
        self.assertEqual(cam.ctrls['zoom_absolute'].value, 0)
        pad, cam, *_ = make(zoom=380)
        self.run_axes(pad, {'righttrigger': 32767}, 2.0)
        self.assertEqual(cam.ctrls['zoom_absolute'].value, 400)
        self.assertTrue(all(0 <= v <= 400 for k, v in cam.writes))

    def test_deadzone_and_fine_stick(self):
        pad, cam, *_ = make(zoom=100)
        self.run_axes(pad, {'righttrigger': 2000, 'righty': 1000}, 1.0)
        self.assertEqual(cam.writes, [])
        self.run_axes(pad, {'righty': -32767}, 1.0, start=3.0)
        self.assertTrue(120 <= cam.ctrls['zoom_absolute'].value <= 145, cam.ctrls['zoom_absolute'].value)
        self.run_axes(pad, {'righty': 32767}, 1.0, start=5.0)
        self.assertTrue(cam.ctrls['zoom_absolute'].value < 100)

    def test_write_rate_is_limited(self):
        pad, cam, *_ = make()
        self.run_axes(pad, {'righttrigger': 32767}, 1.0, step=0.005)
        self.assertLessEqual(len(cam.writes), 1.0 / 0.06 + 2)

    def test_resyncs_to_device_value_after_release(self):
        pad, cam, *_ = make()
        t = self.run_axes(pad, {'righttrigger': 32767}, 0.3)
        pad.update(t, set(), {})
        cam.ctrls['zoom_absolute'].value = 300
        self.run_axes(pad, {'righttrigger': 32767}, 0.1, start=t + 1)
        self.assertGreaterEqual(cam.ctrls['zoom_absolute'].value, 300)


class Presets(unittest.TestCase):
    INI = '[preset_1]\nx=1\n[preset_2]\nx=1\n[preset_3]\nx=1\n[preset_names]\n3 = Desk\n'

    def test_next_prev_named_first(self):
        pad, cam, log, presets = make(self.INI)
        t = 1.0
        for _ in range(4):
            t = tap(pad, t, 'dpright')
        self.assertEqual(presets, ['3', '1', '2', '3'])
        presets.clear()
        t = tap(pad, t, 'dpleft')
        self.assertEqual(presets, ['2'])

    def test_no_presets_saved(self):
        pad, cam, log, presets = make()
        tap(pad, 1.0, 'dpleft')
        self.assertEqual(presets, [])
        self.assertTrue(any('no presets' in l for l in log))

    def test_chord_hold_restores_preset_1_once(self):
        pad, cam, log, presets = make(self.INI)
        t = 1.0
        while t < 4.0:
            pad.update(t, {'back', 'start'}, {})
            t += 0.1
        self.assertEqual(presets, ['1'])
        pad.update(t, set(), {})
        self.assertEqual(cam.writes, [], 'chord must not trigger Reset or Focus')

    def test_chord_short_does_nothing(self):
        pad, cam, log, presets = make(self.INI)
        pad.update(1.0, {'back', 'start'}, {})
        pad.update(1.5, {'back', 'start'}, {})
        pad.update(1.6, {'start'}, {})
        pad.update(1.7, set(), {})
        self.assertEqual((presets, cam.writes), ([], []))

    def test_chord_can_fire_again_after_release(self):
        pad, cam, log, presets = make(self.INI)
        for base in (1.0, 10.0):
            t = base
            while t < base + 2.5:
                pad.update(t, {'back', 'start'}, {})
                t += 0.1
            pad.update(t, set(), {})
        self.assertEqual(presets, ['1', '1'])

    def test_unsaved_preset_1_is_logged(self):
        pad, cam, log, presets = make('[preset_2]\nx=1\n')
        t = 1.0
        while t < 4.0:
            pad.update(t, {'back', 'start'}, {})
            t += 0.1
        self.assertEqual(presets, [])
        self.assertTrue(any('not saved' in l for l in log))


class Map(unittest.TestCase):
    def test_every_default_binding_is_valid_and_listed(self):
        for key, action in m.DEFAULT_PAD.items():
            self.assertTrue(m.valid_action(action), (key, action))
        text = m.format_map(*m.load_profile(configparser.ConfigParser()))
        for want in ('RT', 'LT', 'A ', 'Y ', 'D-pad up', 'Back + Start', 'Left stick click'):
            self.assertIn(want, text)

    def test_pad_label(self):
        self.assertEqual(m.pad_label('Generic X-Box pad'), 'Xbox 360')
        self.assertEqual(m.pad_label('Wireless Controller'), 'Wireless Controller')

    def test_bad_numbers_keep_defaults(self):
        _, settings, steps = m.load_profile(self._cfg('[pad]\nrepeat_ms = fast\n[remote_steps]\n5x = lots\n'))
        self.assertEqual(settings['repeat_ms'], m.DEFAULT_SETTINGS['repeat_ms'])
        self.assertEqual(steps['5x'], 200)

    @staticmethod
    def _cfg(text):
        c = configparser.ConfigParser(interpolation=None)
        c.read_string(text)
        return c


if __name__ == '__main__':
    unittest.main(verbosity=1)
