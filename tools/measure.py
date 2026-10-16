#!/usr/bin/env python3
"""Sharpness, noise and colour cast of still images. Reads image files only.

    tools/measure.py [--crop 0.5] [--tile 48] [--json] IMAGE...

sharp  variance of the Laplacian over the centre crop (grey), higher is sharper
noise  std of the high-pass residual in the flattest tiles (10th percentile), lower is cleaner
r_g, b_g, cct  grey-world: mean R/G, mean B/G and a rough colour temperature from them
luma   mean brightness 0..255
"""
import argparse
import json
import sys

import numpy as np
from PIL import Image


def box3(a):
    p = np.pad(a, 1, mode='edge')
    h, w = a.shape
    return sum(p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)) / 9.0


def laplacian_var(g):
    c = g[1:-1, 1:-1]
    lap = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * c
    return float(lap.var())


def noise_std(g, tile):
    resid = g - box3(g)
    h, w = g.shape
    stds = []
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            stds.append(resid[y:y + tile, x:x + tile].std())
    if not stds:
        return float(resid.std())
    return float(np.percentile(stds, 10))


def cct_from_ratios(r_g, b_g):
    # McCamy on a linearised sRGB grey-world white; coarse, only good for comparing runs
    lin = np.array([r_g, 1.0, b_g]) ** 2.2
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    X, Y, Z = m @ lin
    s = X + Y + Z
    if s <= 0:
        return float('nan')
    x, y = X / s, Y / s
    n = (x - 0.3320) / (0.1858 - y) if y != 0.1858 else float('inf')
    return float(449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33)


def measure(img, crop=0.5, tile=48):
    rgb = np.asarray(img.convert('RGB'), dtype=np.float64)
    h, w, _ = rgb.shape
    ch, cw = int(h * crop), int(w * crop)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    centre = rgb[y0:y0 + ch, x0:x0 + cw]
    grey = centre @ np.array([0.299, 0.587, 0.114])
    mean = rgb.reshape(-1, 3).mean(axis=0)
    g = max(mean[1], 1e-9)
    r_g, b_g = float(mean[0] / g), float(mean[2] / g)
    return {
        'sharp': laplacian_var(grey),
        'noise': noise_std(grey, tile),
        'r_g': r_g,
        'b_g': b_g,
        'cct': cct_from_ratios(r_g, b_g),
        'luma': float((rgb @ np.array([0.299, 0.587, 0.114])).mean()),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('images', nargs='+')
    ap.add_argument('--crop', type=float, default=0.5, help='centre crop fraction for sharpness, 0.1 to 1')
    ap.add_argument('--tile', type=int, default=48)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if not 0.1 <= a.crop <= 1 or a.tile < 4:
        ap.error('--crop must be 0.1 to 1 and --tile at least 4')
    rows = []
    for path in a.images:
        try:
            with Image.open(path) as im:
                rows.append(dict(file=path, **measure(im, a.crop, a.tile)))
        except (OSError, ValueError) as e:
            print(f'measure: {path}: {e}', file=sys.stderr)
            return 1
    if a.json:
        print(json.dumps(rows))
    else:
        for r in rows:
            print(f"{r['file']}\tsharp={r['sharp']:.2f}\tnoise={r['noise']:.3f}\tr_g={r['r_g']:.3f}\tb_g={r['b_g']:.3f}\tcct={r['cct']:.0f}\tluma={r['luma']:.1f}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
