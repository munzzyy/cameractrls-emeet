#!/usr/bin/env python3
"""tools/measure.py on synthetic images, no camera involved."""
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import measure


def img(a):
    return Image.fromarray(np.clip(a, 0, 255).astype('uint8'))


class MeasureTests(unittest.TestCase):
    def test_flat_image_has_no_sharpness_or_noise(self):
        m = measure.measure(img(np.full((240, 320, 3), 100.0)))
        self.assertEqual(m['sharp'], 0.0)
        self.assertEqual(m['noise'], 0.0)
        self.assertAlmostEqual(m['r_g'], 1.0)
        self.assertAlmostEqual(m['luma'], 100.0, places=3)

    def test_noise_tracks_the_injected_sigma(self):
        rng = np.random.default_rng(7)
        quiet = measure.measure(img(100 + rng.normal(0, 2, (240, 320, 3))))['noise']
        loud = measure.measure(img(100 + rng.normal(0, 8, (240, 320, 3))))['noise']
        self.assertGreater(quiet, 0.5)
        self.assertGreater(loud, quiet * 2.5)

    def test_sharper_edges_score_higher(self):
        a = np.zeros((240, 320, 3)); a[:, 160:] = 200
        hard = measure.measure(img(a))['sharp']
        soft = measure.measure(img(measure.box3(a[..., 0])[..., None].repeat(3, 2)))['sharp']
        self.assertGreater(hard, soft)

    def test_colour_cast_ratios(self):
        a = np.zeros((120, 160, 3)); a[..., 0] = 200; a[..., 1] = 100; a[..., 2] = 50
        m = measure.measure(img(a))
        self.assertAlmostEqual(m['r_g'], 2.0, places=2)
        self.assertAlmostEqual(m['b_g'], 0.5, places=2)

    def test_cli_prints_json_and_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 's.png')
            img(np.full((64, 64, 3), 90.0)).save(p)
            ok = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'measure.py'), '--json', p], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn('"sharp"', ok.stdout)
            bad = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'measure.py'), os.path.join(d, 'nope.png')], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn('nope.png', bad.stderr)
            bad = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'measure.py'), '--crop', '0', p], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)


if __name__ == '__main__':
    unittest.main()
