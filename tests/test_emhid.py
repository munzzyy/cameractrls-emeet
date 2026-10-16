import os, pathlib, sys, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import emhid


def pad(s):
    b = bytes.fromhex(s.replace(' ', ''))
    return b + bytes(32 - len(b))


PUSHED = pad('09 04 00 11 00 16 00 16' + ' 00' * 11 + ' 01')


class FrameTests(unittest.TestCase):
    def test_pushed_frame_parses(self):
        head, idx, total, chunk = emhid.parse_v2(PUSHED)
        self.assertEqual((head, idx, total, len(chunk)), (bytes.fromhex('09040011'), 0, 22, 22))

    def test_build_parse_roundtrip(self):
        for n in (0, 1, 2, 23, 24, 25, 48, 49, 100):
            payload = bytes((i * 7 + 3) & 0xFF for i in range(n))
            reps = emhid.build_v2(bytes((9, 4, 0, 0x99)), payload)
            self.assertTrue(all(len(r) == 32 for r in reps))
            self.assertEqual(len(reps), max(1, -(-n // 24)))
            ra = emhid.V2Reassembler()
            out = None
            for r in reps:
                out = ra.feed(r)
            self.assertEqual(out, (bytes((9, 4, 0, 0x99)), payload))

    def test_fragment_headers(self):
        reps = emhid.build_v2(bytes((9, 1, 2, 3)), bytes(range(30)))
        self.assertEqual(reps[0][:8], bytes.fromhex('090102030 01e0018'.replace(' ', '')))
        self.assertEqual(reps[1][:8], bytes.fromhex('0901020301 1e0006'.replace(' ', '')))
        self.assertEqual(reps[1][8:14], bytes(range(24, 30)))

    def test_reassembler_rejects_gaps_and_wrong_start(self):
        reps = emhid.build_v2(bytes((9, 1, 2, 3)), bytes(60))
        ra = emhid.V2Reassembler()
        self.assertIsNone(ra.feed(reps[1]))
        self.assertIsNone(ra.feed(reps[0]))
        self.assertIsNone(ra.feed(reps[2]))

    def test_bad_reports(self):
        with self.assertRaises(ValueError):
            emhid.parse_v2(bytes(32))
        with self.assertRaises(ValueError):
            emhid.parse_v2(b'\x09\x04')
        self.assertIsNone(emhid.V2Reassembler().feed(b'\x07' + bytes(31)))
        with self.assertRaises(ValueError):
            emhid.build_v2(b'\x07\x00\x00\x00')


class CommandTests(unittest.TestCase):
    def test_reads(self):
        self.assertEqual(emhid.get_serial(), [pad('09 01 00 03 00 00 00 00')])
        self.assertEqual(emhid.get_version(), [pad('09 01 00 04 00 00 00 00')])
        self.assertEqual(emhid.get_light_color(), [pad('09 02 02 03 00 01 00 01 00')])

    def test_set_light(self):
        self.assertEqual(emhid.set_light_color(0, 0, 221, 255),
                         [pad('09 02 02 02 00 04 00 04 00 00 dd ff')])
        for bad in ((0, 256, 0, 0), (0, -1, 0, 0)):
            with self.assertRaises(ValueError):
                emhid.set_light_color(*bad)

    def test_decoders(self):
        self.assertEqual(emhid.decode_serial(b'000000000000'), '000000000000')
        self.assertEqual(emhid.decode_version(b'\x02\x11'), '11.02')
        self.assertEqual(emhid.decode_light_color(b'\x00\x00\xdd\xff'), (0, 221, 255))
        with self.assertRaises(ValueError):
            emhid.decode_version(b'\x02')
        with self.assertRaises(ValueError):
            emhid.decode_light_color(b'\x00\x01')


class FakeNode:
    """A pipe pair standing in for hidraw: queued replies come back, writes are recorded."""

    def __init__(self, replies, stale=()):
        self.r, self.w = os.pipe()
        self.written = []
        self.replies = replies
        for rep in stale:
            os.write(self.w, rep)

    def close(self):
        for fd in (self.r, self.w):
            try:
                os.close(fd)
            except OSError:
                pass


class EmeetHidTests(unittest.TestCase):
    def run_with(self, replies, call, stale=()):
        node = FakeNode(replies, stale)
        real_write = os.write
        real_open = os.open
        real_close = os.close

        def fake_open(path, flags, *a):
            return node.r

        def fake_write(fd, data):
            if fd == node.r:
                node.written.append(data)
                for rep in node.replies:
                    real_write(node.w, rep)
                return len(data)
            return real_write(fd, data)

        try:
            with mock.patch('emhid.os.open', fake_open), mock.patch('emhid.os.write', fake_write), \
                    mock.patch('emhid.os.close', lambda fd: None):
                hid = emhid.EmeetHid('/dev/fake')
                hid.TIMEOUT = 0.3
                return call(hid), node.written
        finally:
            node.close()

    def test_version_ignores_pushed_report(self):
        reply = emhid.build_v2(emhid.HEAD_GET_VER, b'\x02\x11')[0]
        out, written = self.run_with([PUSHED, reply], lambda h: h.get_version())
        self.assertEqual(out, '11.02')
        self.assertEqual(written, emhid.get_version())

    def test_light_get_and_set(self):
        got = emhid.build_v2(emhid.HEAD_GET_LIGHT_COLOR, bytes((0, 0, 221, 255)))[0]
        out, _ = self.run_with([PUSHED, got], lambda h: h.get_light())
        self.assertEqual(out, (0, 221, 255))
        ok = emhid.build_v2(emhid.HEAD_SET_LIGHT_COLOR, b'\x20')[0]
        _, written = self.run_with([ok], lambda h: h.set_light(1, 2, 3))
        self.assertEqual(written, emhid.set_light_color(0, 1, 2, 3))

    def test_set_refused(self):
        bad = emhid.build_v2(emhid.HEAD_SET_LIGHT_COLOR, b'\x01')[0]
        with self.assertRaises(emhid.EmeetHidError):
            self.run_with([bad], lambda h: h.set_light(1, 2, 3))

    def test_stale_reports_are_drained(self):
        reply = emhid.build_v2(emhid.HEAD_GET_SN, b'000000000000')[0]
        out, _ = self.run_with([reply], lambda h: h.get_serial(), stale=[emhid.build_v2(emhid.HEAD_GET_SN, b'old')[0]])
        self.assertEqual(out, '000000000000')

    def test_timeout(self):
        with self.assertRaises(emhid.EmeetHidError) as cm:
            self.run_with([PUSHED], lambda h: h.get_serial())
        self.assertIn('did not answer', str(cm.exception))

    def test_no_device_and_no_access(self):
        with mock.patch('emhid.find_hidraw', return_value=None):
            with self.assertRaises(emhid.EmeetHidError) as cm:
                emhid.EmeetHid().ping()
        self.assertIn('not found', str(cm.exception))
        with mock.patch('emhid.os.open', side_effect=PermissionError):
            with self.assertRaises(emhid.EmeetHidError) as cm:
                emhid.EmeetHid('/dev/fake').ping()
        self.assertIn('70-emeet-c60e.rules', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
