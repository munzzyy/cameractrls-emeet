"""Remote check for the EMEET C60E 4K Dual: walk the IR remote, grab a frame after each press
and say what the picture did. Pure stdlib, frames are raw RGB."""

import colorsys, configparser, datetime, logging, math, os, shutil, subprocess, threading, time

GRAB_W, GRAB_H = 320, 180
SMALL_W, SMALL_H = 160, 90
FAKE_FRAMES_ENV = 'CAMERACTRLS_FAKE_FRAMES'
CHECK_SECTION = 'remote_check'

SEEN = ['flipped', 'mirrored', 'zoomed in', 'zoomed out', 'brighter', 'darker',
        'more colour', 'less colour', 'sharper', 'softer', 'effect (colour change)']
NO_CHANGE = 'no change'

ZOOMS = {'zoomed in', 'zoomed out'}
COLOUR = {'more colour', 'less colour', 'effect (colour change)'}

# key, name on the remote, labels that count as working (None: any sign counts)
REMOTE_BUTTONS = [
    ('reset', 'Reset', None),
    ('focus', 'Focus', None),
    ('switch', 'Switch', ZOOMS),
    ('flip', 'Flip', {'flipped'}),
    ('mirror', 'Mirror', {'mirrored'}),
    ('zoom_in', 'Zoom in', {'zoomed in'}),
    ('zoom_out', 'Zoom out', {'zoomed out'}),
    ('sharpness_down', 'Sharpness -', {'softer'}),
    ('sharpness_up', 'Sharpness +', {'sharper'}),
    ('1x', '1X', ZOOMS),
    ('3x', '3X', ZOOMS),
    ('5x', '5X', ZOOMS),
    ('7x', '7X', ZOOMS),
    ('11x', '11X', ZOOMS),
    ('color_down', 'Colour -', {'less colour'}),
    ('color_up', 'Colour +', {'more colour'}),
    ('brightness_down', 'Brightness -', {'darker'}),
    ('brightness_up', 'Brightness +', {'brighter'}),
    ('effect_1', 'Effect 1', COLOUR),
    ('effect_2', 'Effect 2', COLOUR),
    ('effect_3', 'Effect 3', COLOUR),
    ('effect_4', 'Effect 4', COLOUR),
]


class GrabError(Exception):
    pass


class Frame:
    """An RGB frame, 3 bytes per pixel. Statistics are computed on first use."""

    def __init__(self, width, height, rgb):
        if width < 8 or height < 8 or len(rgb) != width * height * 3:
            raise ValueError(f'bad frame: {width}x{height} with {len(rgb)} bytes')
        self.width, self.height, self.rgb = width, height, bytes(rgb)
        self._gray = None
        self._small = None

    @property
    def gray(self):
        if self._gray is None:
            d = self.rgb
            self._gray = [(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]) / 255.0 for i in range(0, len(d), 3)]
        return self._gray

    def small(self):
        """(gray, rgb bytes) halved by 2x2 averaging while wider than SMALL_W."""
        if self._small is None:
            w, h, d = self.width, self.height, self.rgb
            while w > SMALL_W and w % 2 == 0 and h % 2 == 0:
                out = bytearray(w // 2 * h // 2 * 3)
                o = 0
                for y in range(0, h, 2):
                    r0, r1 = y * w * 3, (y + 1) * w * 3
                    for x in range(0, w * 3, 6):
                        for c in range(3):
                            out[o] = (d[r0 + x + c] + d[r0 + x + 3 + c] + d[r1 + x + c] + d[r1 + x + 3 + c] + 2) >> 2
                            o += 1
                d, w, h = bytes(out), w // 2, h // 2
            gray = [(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]) / 255.0 for i in range(0, len(d), 3)]
            self._small = (w, h, gray, d)
        return self._small


def frame_from_ppm(data):
    """Binary P6 PPM, 8 bit."""
    tokens, pos = [], 0
    while len(tokens) < 4:
        while pos < len(data) and data[pos] in b' \t\r\n':
            pos += 1
        if pos < len(data) and data[pos:pos + 1] == b'#':
            while pos < len(data) and data[pos] != 10:
                pos += 1
            continue
        start = pos
        while pos < len(data) and data[pos] not in b' \t\r\n':
            pos += 1
        if start == pos:
            raise ValueError('truncated PPM header')
        tokens.append(data[start:pos])
    pos += 1
    if tokens[0] != b'P6' or tokens[3] != b'255':
        raise ValueError('only 8 bit binary PPM (P6) is read')
    w, h = int(tokens[1]), int(tokens[2])
    return Frame(w, h, data[pos:pos + w * h * 3])


def frame_to_ppm(frame):
    return b'P6\n%d %d\n255\n' % (frame.width, frame.height) + frame.rgb


# ---------------------------------------------------------------- classifier

def _corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sab = saa = sbb = 0.0
    for x, y in zip(a, b):
        x -= ma
        y -= mb
        sab += x * y
        saa += x * x
        sbb += y * y
    if saa < 1e-6 or sbb < 1e-6:
        return 0.0
    return sab / math.sqrt(saa * sbb)


def _hmirror(g, w, h):
    return [v for y in range(h) for v in g[y * w:(y + 1) * w][::-1]]


def _vflip(g, w, h):
    return [v for y in range(h - 1, -1, -1) for v in g[y * w:(y + 1) * w]]


def _crop_scale(g, w, h, s):
    """Centre crop of 1/s of the picture, bilinearly stretched back to w x h."""
    cw, ch = w / s, h / s
    x0, y0 = (w - cw) / 2, (h - ch) / 2
    xs = []
    for x in range(w):
        fx = x0 + (x + 0.5) * cw / w - 0.5
        fx = min(max(fx, 0.0), w - 1.0)
        i = min(int(fx), w - 2)
        xs.append((i, fx - i))
    out = []
    for y in range(h):
        fy = y0 + (y + 0.5) * ch / h - 0.5
        fy = min(max(fy, 0.0), h - 1.0)
        j = min(int(fy), h - 2)
        ty = fy - j
        r0, r1 = g[j * w:(j + 1) * w], g[(j + 1) * w:(j + 2) * w]
        for i, tx in xs:
            top = r0[i] + (r0[i + 1] - r0[i]) * tx
            bot = r1[i] + (r1[i + 1] - r1[i]) * tx
            out.append(top + (bot - top) * ty)
    return out


def _zoom_search(prev, cur, w, h):
    """Best (corr, 'zoomed in'|'zoomed out', scale): zoomed in means the old centre fills the new frame."""
    best = (-1.0, None, 1.0)
    coarse = [1.04, 1.08, 1.13, 1.2, 1.3, 1.4, 1.55, 1.7, 1.9, 2.15, 2.5, 2.9, 3.4, 4.0]
    for label, src, dst in (('zoomed in', prev, cur), ('zoomed out', cur, prev)):
        cand = [(_corr(_crop_scale(src, w, h, s), dst), s) for s in coarse]
        c, s = max(cand)
        for k in (-0.75, -0.5, -0.25, 0.25, 0.5, 0.75):
            s2 = s * (1 + 0.03 * k)
            if s2 > 1.01:
                c2 = _corr(_crop_scale(src, w, h, s2), dst)
                if c2 > c:
                    c, s = c2, s2
        if c > best[0]:
            best = (c, label, s)
    return best


def _edge_energy(frame):
    w, h, g = frame.width, frame.height, frame.gray
    total = 0.0
    for y in range(h - 1):
        row, nxt = y * w, (y + 1) * w
        for x in range(w - 1):
            v = g[row + x]
            total += abs(g[row + x + 1] - v) + abs(g[nxt + x] - v)
    mean = sum(g) / len(g)
    return total / ((w - 1) * (h - 1)) / max(mean, 0.05)


def _colour_stats(rgb):
    """Mean saturation, mean hue (turns) and how much hue there is to average."""
    sat = sx = sy = 0.0
    n = len(rgb) // 3
    for i in range(0, len(rgb), 3):
        hue, s, _ = colorsys.rgb_to_hsv(rgb[i] / 255.0, rgb[i + 1] / 255.0, rgb[i + 2] / 255.0)
        sat += s
        sx += s * math.cos(hue * 2 * math.pi)
        sy += s * math.sin(hue * 2 * math.pi)
    sat /= n
    weight = math.hypot(sx, sy) / n
    hue = (math.atan2(sy, sx) / (2 * math.pi)) % 1.0 if weight > 1e-6 else 0.0
    return sat, hue, weight


class Change:
    def __init__(self, labels, detail):
        self.labels = labels
        self.detail = detail

    @property
    def text(self):
        return ', '.join(self.labels) if self.labels else NO_CHANGE


def classify(prev, cur):
    """What happened to the picture between two frames of the same size."""
    if (prev.width, prev.height) != (cur.width, cur.height):
        raise ValueError('frames differ in size')
    w, h, pg, prgb = prev.small()
    _, _, cg, crgb = cur.small()
    detail = {}
    labels = []

    ident = _corr(pg, cg)
    detail['identity'] = round(ident, 3)
    textured = _corr(pg, pg) > 0 and _corr(cg, cg) > 0
    zoomed = False
    if textured:
        scores = {'flipped': max(_corr(_vflip(pg, w, h), cg), _corr(_vflip(_hmirror(pg, w, h), w, h), cg)),
                  'mirrored': _corr(_hmirror(pg, w, h), cg)}
        detail.update({k: round(v, 3) for k, v in scores.items()})
        name, top = max(scores.items(), key=lambda kv: kv[1])
        other = min(scores.values())
        if top >= 0.6 and top >= ident + 0.2 and top >= other + 0.05:
            labels.append(name)
        else:
            zc, zlabel, zs = _zoom_search(pg, cg, w, h)
            detail['zoom'] = (round(zc, 3), zlabel, round(zs, 3))
            if zc >= 0.7 and zc >= ident + 0.03:
                labels.append(zlabel)
                zoomed = True
        if ident <= -0.5:
            labels.append('effect (colour change)')
    geometry = bool(labels) and labels[0] != 'effect (colour change)'
    slack = 3.0 if zoomed else 1.0

    pm, cm = sum(pg) / len(pg), sum(cg) / len(cg)
    dl = cm - pm
    detail['luma'] = round(dl, 3)
    if abs(dl) >= 0.025 * slack and abs(dl) >= 0.05 * max(pm, 0.05):
        labels.append('brighter' if dl > 0 else 'darker')

    ps, ph, pw = _colour_stats(prgb)
    cs, ch, cw = _colour_stats(crgb)
    detail['saturation'] = (round(ps, 3), round(cs, 3))
    ratio = (cs + 0.01) / (ps + 0.01)
    if abs(cs - ps) >= 0.03 * slack and not 0.9 <= ratio <= 1.1:
        labels.append('more colour' if cs > ps else 'less colour')
    hshift = 0.0
    if pw > 0.02 and cw > 0.02:
        hshift = abs(ch - ph)
        hshift = min(hshift, 1 - hshift)
    detail['hue_shift'] = round(hshift, 3)
    strict = 2.0 if zoomed else 1.0
    effect = ((hshift >= 0.06 * strict and min(ps, cs) >= 0.06) or (ratio <= 0.35 / strict and ps >= 0.1)
              or (ratio >= 2.8 * strict and cs >= 0.1))
    if effect and 'effect (colour change)' not in labels:
        labels.append('effect (colour change)')

    if not zoomed and not geometry:
        pe, ce = _edge_energy(prev), _edge_energy(cur)
        detail['edges'] = (round(pe, 4), round(ce, 4))
        if pe > 1e-4:
            r = ce / pe
            if r >= 1.07:
                labels.append('sharper')
            elif r <= 0.93:
                labels.append('softer')
    return Change(labels, detail)


# ---------------------------------------------------------------- the walk

class StepResult:
    def __init__(self, key, name, change, heard, status, verdict):
        self.key, self.name, self.change = key, name, change
        self.heard, self.status, self.verdict = heard, status, verdict

    @property
    def seen(self):
        return self.change.text


def judge(expected, change, heard):
    """(status, plain verdict) for one button."""
    labels = set(change.labels)
    if expected is None:
        if labels or heard:
            return 'works', 'works' + ('' if labels else ', the camera acknowledged it')
    elif labels & expected:
        return 'works', 'works'
    if heard:
        return 'heard', 'the camera acknowledged it, the picture test could not confirm the change'
    if labels:
        return 'other', f'the picture changed ({change.text}) but not the way this button should'
    return 'none', 'no sign: nothing changed and the camera reported nothing, check the press and the remote\'s battery'


def run_check(grab, say, heard_count, wait=3.0, sleep=time.sleep, buttons=REMOTE_BUTTONS, cancelled=lambda: False):
    """Press each button on cue. grab() -> Frame, say(index, total, name, phase) with phase 'press' or 'done',
    heard_count() -> how many HID pushes so far. Returns a list of StepResult."""
    total = len(buttons)
    prev = grab()
    results = []
    for i, (key, name, expected) in enumerate(buttons):
        if cancelled():
            break
        before = heard_count()
        say(i, total, name, 'press')
        sleep(wait)
        cur = grab()
        change = classify(prev, cur)
        heard = heard_count() > before
        status, verdict = judge(expected, change, heard)
        results.append(StepResult(key, name, change, heard, status, verdict))
        say(i, total, name, 'done')
        prev = cur
    return results


def summary(results):
    ok = sum(r.status == 'works' for r in results)
    text = f'{ok} of {len(results)} buttons confirmed'
    bad = [r.name for r in results if r.status != 'works']
    if bad:
        text += '; not confirmed: ' + ', '.join(bad)
    return text


def format_table(results):
    rows = [('Button', 'Seen', 'Camera heard', 'Verdict')]
    rows += [(r.name, r.seen, 'yes' if r.heard else 'no', r.verdict) for r in results]
    widths = [max(len(row[c]) for row in rows) for c in range(3)]
    lines = []
    for n, row in enumerate(rows):
        lines.append('  '.join(row[c].ljust(widths[c]) for c in range(3)) + '  ' + row[3])
        if n == 0:
            lines.append('  '.join('-' * widths[c] for c in range(3)) + '  ' + '-' * 7)
    lines.append('')
    lines.append(summary(results))
    return '\n'.join(lines)


# ---------------------------------------------------------------- the ini

def _read_ini(path):
    config = configparser.ConfigParser(interpolation=None)
    config.read(path, encoding='utf-8')
    return config


def ini_get(path, section, key, default=None):
    try:
        return _read_ini(path).get(section, key, fallback=default)
    except (configparser.Error, OSError) as e:
        logging.warning(f'emcheck: can\'t read {path}: {e}')
        return default


def ini_set(path, section, values, replace=False):
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    config = _read_ini(path)
    if replace or section not in config:
        config[section] = {}
    config[section].update(values)
    with open(path, 'w', encoding='utf-8') as f:
        config.write(f)


def save_result(path, results, when=None):
    when = when or datetime.datetime.now()
    values = {'date': when.strftime('%Y-%m-%d %H:%M')}
    for r in results:
        values[r.key] = f'{r.status}; seen: {r.seen}; heard: {"yes" if r.heard else "no"}'
    ini_set(path, CHECK_SECTION, values, replace=True)


def last_result_text(path):
    """One line for the Remote category."""
    try:
        config = _read_ini(path)
    except (configparser.Error, OSError):
        return 'not run yet, press Check remote'
    if CHECK_SECTION not in config or 'date' not in config[CHECK_SECTION]:
        return 'not run yet, press Check remote'
    section = config[CHECK_SECTION]
    names = {k: n for k, n, _ in REMOTE_BUTTONS}
    done = [k for k in names if k in section]
    ok = [k for k in done if section[k].startswith('works')]
    text = f'{section["date"]}: {len(ok)} of {len(done)} buttons confirmed'
    bad = [names[k] for k in done if k not in ok]
    if bad:
        text += '; not confirmed: ' + ', '.join(bad)
    return text


# ---------------------------------------------------------------- frame sources

class FakeGrabber:
    """Reads numbered .ppm files from a directory in order, then repeats the last one."""

    def __init__(self, directory):
        self.files = sorted(os.path.join(directory, n) for n in os.listdir(directory) if n.endswith('.ppm'))
        if not self.files:
            raise GrabError(f'no .ppm frames in {directory}')
        self.n = 0

    def grab(self):
        path = self.files[min(self.n, len(self.files) - 1)]
        self.n += 1
        with open(path, 'rb') as f:
            return frame_from_ppm(f.read())

    def close(self):
        pass


class FfmpegGrabber:
    def __init__(self, device, fmt):
        self.exe = shutil.which('ffmpeg')
        if not self.exe:
            raise GrabError('ffmpeg is not installed')
        self.device, self.fmt = device, fmt

    def grab(self):
        w, h = self.fmt['width'], self.fmt['height']
        cmd = [self.exe, '-v', 'error', '-f', 'v4l2']
        if self.fmt['pixelformat'] == 'MJPG':
            cmd += ['-input_format', 'mjpeg']
        cmd += ['-video_size', f'{w}x{h}', '-framerate', str(self.fmt['fps']), '-i', self.device,
                '-frames:v', '8', '-vf', f'scale={GRAB_W}:{GRAB_H}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            raise GrabError('ffmpeg timed out reading the camera')
        size = GRAB_W * GRAB_H * 3
        if p.returncode != 0 or len(p.stdout) < size:
            err = p.stderr.decode(errors='replace').strip()
            raise GrabError(('The camera is busy. ' if 'busy' in err.lower() else '') + (err or 'ffmpeg got no frame'))
        return Frame(GRAB_W, GRAB_H, p.stdout[-size:])

    def close(self):
        pass


def make_grabber(device, fmt):
    """Fake frames if CAMERACTRLS_FAKE_FRAMES is set, else GStreamer, else ffmpeg."""
    fake = os.environ.get(FAKE_FRAMES_ENV)
    if fake:
        return FakeGrabber(fake)
    try:
        import empreview
    except (ImportError, ValueError) as e:
        logging.info(f'emcheck: no GStreamer python bindings ({e}), trying ffmpeg')
        empreview = None
    if empreview is not None:
        try:
            return empreview.GstGrabber(device, fmt)
        except empreview.PreviewUnavailable as e:
            logging.info(f'emcheck: no GStreamer grab ({e}), trying ffmpeg')
    try:
        return FfmpegGrabber(device, fmt)
    except GrabError:
        raise GrabError('Neither GStreamer (v4l2src, jpegdec) nor ffmpeg is available here, so the picture '
                        'can\'t be read. Press the buttons and watch the preview instead.')


class HeardCounter:
    """Counts the camera's HID pushes (one per remote press) from a RemoteWatcher callback."""

    def __init__(self):
        self.lock = threading.Lock()
        self.n = 0

    def push(self):
        with self.lock:
            self.n += 1

    def __call__(self):
        with self.lock:
            return self.n
