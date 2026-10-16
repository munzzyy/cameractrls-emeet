"""EMEET vendor HID channel (V2 framing, report id 9) for the C60E 4K Dual, firmware 11.02.

Only the four commands this firmware answers are here: serial, version, light colour get/set.
"""
import glob, logging, os, select, threading, time

UDEV_RULE = '/etc/udev/rules.d/70-emeet-c60e.rules'
HID_ID = '0000328F:000000E1'
NO_ACCESS_NOTE = f'No access to the camera\'s HID node. Install the udev rule {UDEV_RULE} (see README-emeet.md), then replug the camera.'
NO_DEVICE_NOTE = 'The camera\'s HID node was not found.'

REPORT_ID = 0x09
REPORT_LEN = 32
HEADER_LEN = 8
MAX_PAYLOAD = 24
SET_OK = 0x20
LED_ID = 0

HEAD_GET_SN = bytes((REPORT_ID, 1, 0, 3))
HEAD_GET_VER = bytes((REPORT_ID, 1, 0, 4))
HEAD_SET_LIGHT_COLOR = bytes((REPORT_ID, 2, 2, 2))
HEAD_GET_LIGHT_COLOR = bytes((REPORT_ID, 2, 2, 3))
HEAD_REMOTE_PUSH = bytes((REPORT_ID, 4, 0, 0x11))


class EmeetHidError(Exception):
    pass


def build_v2(head, payload=b''):
    if len(head) != 4 or head[0] != REPORT_ID:
        raise ValueError('V2 head must be 4 bytes starting with 0x09')
    total = len(payload)
    if total > 0xFFFF:
        raise ValueError('payload too long')
    chunks = [payload[i:i + MAX_PAYLOAD] for i in range(0, total, MAX_PAYLOAD)] or [b'']
    reports = []
    for idx, chunk in enumerate(chunks):
        if idx > 255:
            raise ValueError('too many fragments')
        rep = bytearray(REPORT_LEN)
        rep[0:4] = head
        rep[4] = idx
        rep[5] = total & 0xFF
        rep[6] = total >> 8
        rep[7] = len(chunk)
        rep[HEADER_LEN:HEADER_LEN + len(chunk)] = chunk
        reports.append(bytes(rep))
    return reports


def parse_v2(report):
    if len(report) < HEADER_LEN:
        raise ValueError('short report')
    if report[0] != REPORT_ID:
        raise ValueError('not a V2 report (id != 9)')
    chunk_len = report[7]
    if len(report) - HEADER_LEN < chunk_len:
        raise ValueError('chunk longer than report')
    return (bytes(report[0:4]), report[4], report[5] | (report[6] << 8),
            bytes(report[HEADER_LEN:HEADER_LEN + chunk_len]))


class V2Reassembler:
    """Index 0 starts a message, later fragments need the same head and index + 1."""

    def __init__(self):
        self._head = None
        self._idx = 0
        self._total = 0
        self._buf = bytearray()

    def feed(self, report):
        try:
            head, index, total, chunk = parse_v2(report)
        except ValueError:
            return None
        if self._head is not None and (head != self._head or index != self._idx + 1):
            self._head = None
        if self._head is None:
            if index != 0:
                return None
            self._head, self._total, self._buf = head, total, bytearray()
        self._idx = index
        self._buf += chunk
        if len(self._buf) >= self._total:
            head, payload = self._head, bytes(self._buf[:self._total])
            self._head = None
            return head, payload
        return None


def get_serial():
    return build_v2(HEAD_GET_SN)


def get_version():
    return build_v2(HEAD_GET_VER)


def get_light_color(led_id=LED_ID):
    return build_v2(HEAD_GET_LIGHT_COLOR, bytes((led_id,)))


def set_light_color(led_id, r, g, b):
    for v in (led_id, r, g, b):
        if not 0 <= v <= 255:
            raise ValueError('colour byte out of range')
    return build_v2(HEAD_SET_LIGHT_COLOR, bytes((led_id, r, g, b)))


def decode_serial(payload):
    return payload.rstrip(b'\0').decode('ascii', 'replace')


def decode_version(payload):
    if len(payload) < 2:
        raise ValueError('version reply needs 2 bytes')
    return f'{payload[1]:02x}.{payload[0]:02x}'


def decode_light_color(payload):
    if len(payload) < 4:
        raise ValueError('light colour reply needs 4 bytes')
    return payload[1], payload[2], payload[3]


def find_hidraw():
    for dev in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
        try:
            with open(os.path.join(dev, 'device', 'uevent')) as f:
                if any(line.startswith('HID_ID=') and HID_ID in line for line in f):
                    return '/dev/' + os.path.basename(dev)
        except OSError:
            continue
    return None


class EmeetHid:
    TIMEOUT = 1.0

    def __init__(self, path=None):
        self.path = path

    def _exchange(self, reports, head):
        path = self.path or find_hidraw()
        if path is None:
            raise EmeetHidError(NO_DEVICE_NOTE)
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            raise EmeetHidError(NO_ACCESS_NOTE)
        except OSError as e:
            raise EmeetHidError(f'{NO_DEVICE_NOTE} ({path}: {e.strerror})')
        try:
            while select.select([fd], [], [], 0)[0]:
                os.read(fd, REPORT_LEN)
            for r in reports:
                os.write(fd, r)
            asm = V2Reassembler()
            deadline = time.monotonic() + self.TIMEOUT
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise EmeetHidError('The camera did not answer on its HID node.')
                if not select.select([fd], [], [], left)[0]:
                    continue
                data = os.read(fd, REPORT_LEN)
                if data[:4] != head:
                    continue
                done = asm.feed(data)
                if done is not None:
                    return done[1]
        except PermissionError:
            raise EmeetHidError(NO_ACCESS_NOTE)
        except OSError as e:
            raise EmeetHidError(f'HID I/O failed: {e.strerror}')
        finally:
            os.close(fd)

    def ping(self):
        return self.get_version()

    def get_serial(self):
        return decode_serial(self._exchange(get_serial(), HEAD_GET_SN))

    def get_version(self):
        try:
            return decode_version(self._exchange(get_version(), HEAD_GET_VER))
        except ValueError as e:
            raise EmeetHidError(str(e))

    def get_light(self):
        try:
            return decode_light_color(self._exchange(get_light_color(), HEAD_GET_LIGHT_COLOR))
        except ValueError as e:
            raise EmeetHidError(str(e))

    def set_light(self, r, g, b):
        reply = self._exchange(set_light_color(LED_ID, r, g, b), HEAD_SET_LIGHT_COLOR)
        if reply[:1] != bytes((SET_OK,)):
            raise EmeetHidError(f'The camera refused the light colour (status {reply.hex() or "none"}).')


def open_hid_readonly(path=None):
    path = path or find_hidraw()
    if path is None:
        raise EmeetHidError(NO_DEVICE_NOTE)
    try:
        return os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except PermissionError:
        raise EmeetHidError(NO_ACCESS_NOTE)
    except OSError as e:
        raise EmeetHidError(f'{NO_DEVICE_NOTE} ({path}: {e.strerror})')


def read_pushes(fd, on_push, stop, poll=0.5):
    """Call on_push() for every report headed 09 04 00 11 until stop is set or fd closes."""
    while not stop.is_set():
        if not select.select([fd], [], [], poll)[0]:
            continue
        try:
            data = os.read(fd, REPORT_LEN)
        except BlockingIOError:
            continue
        if not data:
            return
        if data[:4] == HEAD_REMOTE_PUSH:
            on_push()


class RemoteWatcher(threading.Thread):
    """Reads the camera's unsolicited remote-press reports, never raises into the caller."""

    def __init__(self, on_push, path=None):
        super().__init__(daemon=True)
        self.on_push = on_push
        self.path = path
        self.stop_event = threading.Event()

    def run(self):
        try:
            fd = open_hid_readonly(self.path)
        except EmeetHidError as e:
            logging.debug(f'remote watcher: {e}')
            return
        try:
            read_pushes(fd, self.on_push, self.stop_event)
        except OSError as e:
            logging.debug(f'remote watcher stopped: {e.strerror}')
        finally:
            os.close(fd)

    def stop(self):
        self.stop_event.set()
