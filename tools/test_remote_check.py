#!/usr/bin/env python3
"""Tests for emcheck: the picture classifier on synthetic frames and the --remote-check run on fake frames."""
import colorsys, functools, os, pathlib, random, subprocess, sys, tempfile, unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import emcheck
from emcheck import Frame

W, H = emcheck.GRAB_W, emcheck.GRAB_H


@functools.lru_cache(maxsize=None)
def scene():
    """An asymmetric, textured, saturated picture."""
    rnd = random.Random(7)
    px = [[(40 + x * 90 // W, 60 + y * 80 // H, 130 - x * 60 // W) for x in range(W)] for y in range(H)]
    for _ in range(40):
        x0, y0 = rnd.randrange(W - 30), rnd.randrange(H - 20)
        w, h = rnd.randrange(8, 60), rnd.randrange(6, 40)
        col = tuple(rnd.randrange(30, 250) for _ in range(3))
        for y in range(y0, min(H, y0 + h)):
            for x in range(x0, min(W, x0 + w)):
                px[y][x] = col
    for y in range(20, 70):
        for x in range(30, 120):
            if (x // 3 + y // 3) % 2:
                px[y][x] = (230, 40, 40)
    flat = bytearray()
    for row in px:
        for p in row:
            flat += bytes(p)
    return bytes(flat)


def channels(rgb):
    return [[float(rgb[i + c]) for i in range(0, len(rgb), 3)] for c in range(3)]


def join(ch):
    out = bytearray()
    for r, g, b in zip(*ch):
        out += bytes((min(255, max(0, int(r + .5))), min(255, max(0, int(g + .5))), min(255, max(0, int(b + .5)))))
    return bytes(out)


def box_blur(plane):
    out = []
    for y in range(H):
        for x in range(W):
            tot = n = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < H and 0 <= xx < W:
                        tot += plane[yy * W + xx]
                        n += 1
            out.append(tot / n)
    return out


def render(base, zoom=1.0, rot=False, mirror=False, sharp=0, gain=1.0, sat=1.0, effect=None, noise=0, seed=1):
    ch = channels(base)
    if zoom != 1.0:
        ch = [emcheck._crop_scale(p, W, H, zoom) for p in ch]
    if rot:
        ch = [emcheck._vflip(emcheck._hmirror(p, W, H), W, H) for p in ch]
    if mirror:
        ch = [emcheck._hmirror(p, W, H) for p in ch]
    if sharp:
        blurred = [box_blur(p) for p in ch]
        ch = blurred if sharp < 0 else [[min(255, max(0, 2 * a - b)) for a, b in zip(p, q)] for p, q in zip(ch, blurred)]
    px = list(zip(*ch))
    out = []
    rnd = random.Random(seed)
    for r, g, b in px:
        lum = .299 * r + .587 * g + .114 * b
        r, g, b = (lum + (c - lum) * sat for c in (r, g, b))
        if effect == 'gray':
            r = g = b = lum
        elif effect in ('sepia', 'negative'):
            r, g, b = lum * 1.07, lum * .74, lum * .43
            if effect == 'negative':
                r, g, b = 255 - r, 255 - g, 255 - b
        elif effect == 'hue':
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            r, g, b = (c * 255 for c in colorsys.hsv_to_rgb((h + 1 / 3) % 1, s, v))
        n = rnd.uniform(-noise, noise) if noise else 0
        out.append((r * gain + n, g * gain + n, b * gain + n))
    return Frame(W, H, join(zip(*out)))


# state changes per button, in the order of REMOTE_BUTTONS, starting from a state Reset undoes
START = dict(zoom=1.0, gain=.8)
STEPS = [
    ('reset', dict(gain=1.0)),
    ('focus', dict(sharp=-1)),
    ('switch', dict(zoom=1.6, sharp=0)),
    ('flip', dict(rot=True)),
    ('mirror', dict(mirror=True)),
    ('zoom_in', dict(zoom=1.9)),
    ('zoom_out', dict(zoom=1.6)),
    ('sharpness_down', dict(sharp=-1)),
    ('sharpness_up', dict(sharp=0)),
    ('1x', dict(zoom=1.0)),
    ('3x', dict(zoom=2.0)),
    ('5x', dict(zoom=2.6)),
    ('7x', dict(zoom=3.0)),
    ('11x', dict(zoom=3.2)),
    ('color_down', dict(sat=.55)),
    ('color_up', dict(sat=1.0)),
    ('brightness_down', dict(gain=.75)),
    ('brightness_up', dict(gain=1.0)),
    ('effect_1', dict(effect='gray')),
    ('effect_2', dict(effect='sepia')),
    ('effect_3', dict(effect='negative')),
    ('effect_4', dict(effect='hue')),
]


@functools.lru_cache(maxsize=None)
def sequence(base, noise=0):
    state = dict(START)
    frames = [render(base, **state, noise=noise, seed=0)]
    for n, (key, change) in enumerate(STEPS, 1):
        state.update(change)
        frames.append(render(base, **state, noise=noise, seed=n))
    return frames


class ClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = scene()
        cls.frames = sequence(cls.base)
        cls.noisy = sequence(cls.base, noise=2)

    def check_walk(self, frames):
        keys = [k for k, _, _ in emcheck.REMOTE_BUTTONS]
        self.assertEqual(keys, [k for k, _ in STEPS])
        for i, (key, name, expected) in enumerate(emcheck.REMOTE_BUTTONS):
            change = emcheck.classify(frames[i], frames[i + 1])
            status, _ = emcheck.judge(expected, change, False)
            self.assertEqual(status, 'works', f'{name}: saw {change.text} {change.detail}')

    def test_every_button_is_seen_on_clean_frames(self):
        self.check_walk(self.frames)

    def test_every_button_is_seen_through_sensor_noise(self):
        self.check_walk(self.noisy)

    def test_exact_labels(self):
        want = {'flip': 'flipped', 'mirror': 'mirrored', 'zoom_in': 'zoomed in', 'zoom_out': 'zoomed out',
                'sharpness_down': 'softer', 'sharpness_up': 'sharper', 'color_down': 'less colour',
                'color_up': 'more colour', 'brightness_down': 'darker', 'brightness_up': 'brighter'}
        for i, (key, _, _) in enumerate(emcheck.REMOTE_BUTTONS):
            if key in want:
                self.assertIn(want[key], emcheck.classify(self.frames[i], self.frames[i + 1]).labels, key)

    def test_identical_and_noisy_frames_are_no_change(self):
        f = self.frames[0]
        self.assertEqual(emcheck.classify(f, f).text, 'no change')
        a, b = render(self.base, noise=2, seed=1), render(self.base, noise=2, seed=2)
        self.assertEqual(emcheck.classify(a, b).text, 'no change', emcheck.classify(a, b).detail)

    def test_flat_frames_do_not_crash_or_claim_geometry(self):
        black = Frame(W, H, bytes(W * H * 3))
        grey = Frame(W, H, bytes([120]) * (W * H * 3))
        for a, b in ((black, black), (black, grey), (grey, self.frames[0])):
            labels = emcheck.classify(a, b).labels
            self.assertFalse({'flipped', 'mirrored', 'zoomed in', 'zoomed out'} & set(labels), labels)

    def test_size_mismatch_raises(self):
        with self.assertRaises(ValueError):
            emcheck.classify(self.frames[0], Frame(16, 16, bytes(16 * 16 * 3)))

    def test_bad_frames_are_refused(self):
        with self.assertRaises(ValueError):
            Frame(4, 4, bytes(48))
        with self.assertRaises(ValueError):
            Frame(W, H, b'x')

    def test_ppm_round_trip_and_garbage(self):
        f = self.frames[3]
        g = emcheck.frame_from_ppm(emcheck.frame_to_ppm(f))
        self.assertEqual((g.width, g.height, g.rgb), (f.width, f.height, f.rgb))
        for bad in (b'', b'P6', b'P5\n2 2\n255\n', b'P6\n99999 99999\n255\nxx'):
            with self.assertRaises(ValueError):
                emcheck.frame_from_ppm(bad)

    def test_judge_uses_the_camera_push_when_the_picture_is_unclear(self):
        none = emcheck.Change([], {})
        self.assertEqual(emcheck.judge({'flipped'}, none, True)[0], 'heard')
        self.assertEqual(emcheck.judge({'flipped'}, none, False)[0], 'none')
        self.assertEqual(emcheck.judge({'flipped'}, emcheck.Change(['darker'], {}), False)[0], 'other')
        self.assertEqual(emcheck.judge(None, none, False)[0], 'none')
        self.assertEqual(emcheck.judge(None, none, True)[0], 'works')


class RunTests(unittest.TestCase):
    def test_run_check_with_pushes_and_a_scripted_source(self):
        base = scene()
        frames = iter(sequence(base))
        heard = emcheck.HeardCounter()
        cues = []

        def say(i, total, name, phase):
            cues.append((i, total, name, phase))
            if phase == 'press' and name == 'Flip':
                heard.push()

        def sleeper(seconds):
            self.assertEqual(seconds, 0.25)

        results = emcheck.run_check(lambda: next(frames), say, heard, wait=0.25, sleep=sleeper)
        self.assertEqual(len(results), 22)
        self.assertEqual(cues[0], (0, 22, 'Reset', 'press'))
        flip = results[3]
        self.assertTrue(flip.heard)
        self.assertEqual(flip.status, 'works')
        self.assertIn('Flip', emcheck.format_table(results))

    def test_cancel_stops_early(self):
        frames = iter(sequence(scene()))
        results = emcheck.run_check(lambda: next(frames), lambda *a: None, lambda: 0, wait=0, sleep=lambda s: None,
                                    cancelled=lambda: True)
        self.assertEqual(results, [])

    def test_save_and_last_result(self):
        frames = iter(sequence(scene()))
        results = emcheck.run_check(lambda: next(frames), lambda *a: None, lambda: 0, wait=0, sleep=lambda s: None)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'sub', 'cam.ini')
            self.assertTrue(emcheck.last_result_text(path).startswith('not run yet'))
            emcheck.ini_set(path, 'ui', {'preview': 'on'})
            emcheck.save_result(path, results)
            self.assertEqual(emcheck.ini_get(path, 'ui', 'preview'), 'on')
            text = emcheck.last_result_text(path)
            self.assertRegex(text, r'^\d{4}-\d\d-\d\d \d\d:\d\d: \d+ of 22 buttons confirmed')


class CliTests(unittest.TestCase):
    def test_remote_check_cli_with_fake_frames(self):
        device = os.environ.get('CAMERA', '/dev/video0')
        with tempfile.TemporaryDirectory() as d:
            frames = pathlib.Path(d, 'frames')
            frames.mkdir()
            for n, f in enumerate(sequence(scene())):
                (frames / f'{n:03d}.ppm').write_bytes(emcheck.frame_to_ppm(f))
            env = {**os.environ, 'CAMERACTRLS_FAKE_FRAMES': str(frames), 'XDG_CONFIG_HOME': str(pathlib.Path(d, 'cfg'))}
            p = subprocess.run([sys.executable, str(ROOT / 'cameractrls.py'), '-d', device, '--remote-check', '--check-wait', '0'],
                               env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            out = p.stdout
            self.assertIn('Press Flip on the remote now', out)
            self.assertRegex(out, r'Flip\s+flipped\s+no\s+works')
            self.assertRegex(out, r'Mirror\s+mirrored\s+no\s+works')
            self.assertIn('22 of 22 buttons confirmed', out)
            ini = list(pathlib.Path(d, 'cfg').rglob('*.ini'))
            self.assertEqual(len(ini), 1)
            self.assertIn('[remote_check]', ini[0].read_text())
            self.assertIn('flip = works; seen: flipped', ini[0].read_text())
            p = subprocess.run([sys.executable, str(ROOT / 'cameractrls.py'), '-d', device, '-l'],
                               env=env, capture_output=True, text=True, timeout=60)
            self.assertRegex(p.stdout, r'emeet_remote_check_info = \d{4}-\d\d-\d\d \d\d:\d\d: 22 of 22')

    def test_bad_wait_is_refused(self):
        p = subprocess.run([sys.executable, str(ROOT / 'cameractrls.py'), '--remote-check', '--check-wait', 'soon'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
