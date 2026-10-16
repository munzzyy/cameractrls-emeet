"""Mean luma of a raw frame and the watchdog that spots the C60E's dark exposure state."""

SAMPLE_PIXELS = 4000
PLANAR_Y = ('I420', 'YV12', 'NV12', 'NV21', 'GRAY8')
RGB_ORDER = {'RGB': (0, 1, 2, 3), 'BGR': (2, 1, 0, 3), 'RGBx': (0, 1, 2, 4), 'BGRx': (2, 1, 0, 4),
             'RGBA': (0, 1, 2, 4), 'BGRA': (2, 1, 0, 4), 'xRGB': (1, 2, 3, 4), 'ARGB': (1, 2, 3, 4),
             'xBGR': (3, 2, 1, 4), 'ABGR': (3, 2, 1, 4)}


def mean_luma(data, fmt, width, height, stride=None):
    """Mean brightness 0..255 from a strided sample of one frame, None when the format is not handled."""
    if width <= 0 or height <= 0:
        return None
    view = memoryview(data)
    if fmt in PLANAR_Y:
        stride = stride or width
        rows = view[:stride * height]
        if len(rows) < stride * height:
            return None
        step = max(1, (width * height) // SAMPLE_PIXELS)
        picked = [rows[y * stride + x] for y in range(0, height, max(1, height // 40)) for x in range(0, width, max(1, step))]
    elif fmt in ('YUY2', 'UYVY'):
        stride = stride or width * 2
        if len(view) < stride * height:
            return None
        off = 0 if fmt == 'YUY2' else 1
        picked = [view[y * stride + x * 2 + off] for y in range(0, height, max(1, height // 40))
                  for x in range(0, width, max(1, width // 100))]
    elif fmt in RGB_ORDER:
        r, g, b, bpp = RGB_ORDER[fmt]
        stride = stride or width * bpp
        if len(view) < stride * height:
            return None
        picked = []
        for y in range(0, height, max(1, height // 40)):
            for x in range(0, width, max(1, width // 100)):
                i = y * stride + x * bpp
                picked.append((54 * view[i + r] + 183 * view[i + g] + 19 * view[i + b]) >> 8)
    else:
        return None
    return sum(picked) / len(picked) if picked else None


class DarkWatchdog:
    """feed() one luma sample at a time. Returns True once when the picture has stayed below `dark` for `hold`
    seconds after being more than twice as bright within the last `window` seconds. Needs a recovery
    (a sample at or above `dark`) before it can fire again."""

    def __init__(self, dark=22.0, hold=4.0, window=60.0):
        self.dark, self.hold, self.window = dark, hold, window
        self.samples = []
        self.dark_since = None
        self.fired = False

    def feed(self, now, luma):
        if luma is None:
            return False
        self.samples.append((now, luma))
        self.samples = [(t, v) for t, v in self.samples if now - t <= self.window]
        if luma >= self.dark:
            self.dark_since = None
            self.fired = False
            return False
        if self.dark_since is None:
            self.dark_since = now
        if self.fired or now - self.dark_since < self.hold:
            return False
        before = max((v for t, v in self.samples if t < self.dark_since), default=0.0)
        if before <= 2 * luma:
            return False
        self.fired = True
        return True

    def reset(self):
        self.samples = []
        self.dark_since = None
        self.fired = False
