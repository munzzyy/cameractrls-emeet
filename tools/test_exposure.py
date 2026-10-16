import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import unittest

import errno
from unittest import mock

import cameractrls as m
import emwatch


class FakeCtrl:
    def __init__(self, fail_writes=0, stuck=None):
        self.value = 'aperture_priority_mode'
        self.fail_writes = fail_writes
        self.stuck = stuck
        self.writes = []

    def write(self, v):
        self.writes.append(v)
        if self.fail_writes > 0:
            self.fail_writes -= 1
            return
        self.value = self.stuck if self.stuck is not None else v

    def read(self):
        return self.value


class WriteVerified(unittest.TestCase):
    def run_it(self, ctrl, retries=3):
        sleeps = []
        ok = m.write_verified(ctrl.write, ctrl.read, 'manual_mode', retries, 0.2, sleeps.append)
        return ok, sleeps

    def test_first_write_sticks(self):
        c = FakeCtrl()
        ok, sleeps = self.run_it(c)
        self.assertTrue(ok)
        self.assertEqual((c.writes, sleeps), (['manual_mode'], []))

    def test_first_write_fails_second_sticks(self):
        c = FakeCtrl(fail_writes=1)
        ok, sleeps = self.run_it(c)
        self.assertTrue(ok)
        self.assertEqual((c.writes, sleeps), (['manual_mode'] * 2, [0.2]))

    def test_three_failures_then_success_on_the_last_retry(self):
        c = FakeCtrl(fail_writes=3)
        ok, sleeps = self.run_it(c)
        self.assertTrue(ok)
        self.assertEqual(len(c.writes), 4)

    def test_gives_up_after_three_retries(self):
        c = FakeCtrl(stuck='aperture_priority_mode')
        ok, sleeps = self.run_it(c)
        self.assertFalse(ok)
        self.assertEqual((len(c.writes), sleeps), (4, [0.2, 0.2, 0.2]))

    def test_unreadable_counts_as_not_stuck(self):
        ok = m.write_verified(lambda v: None, lambda: None, 'manual_mode', 3, 0, lambda s: None)
        self.assertFalse(ok)


class FakeV4l:
    def __init__(self, ctrl):
        self.ctrls = [type('C', (), {'text_id': 'auto_exposure'})()]
        self.ctrl = ctrl

    def read_back(self, c):
        return self.ctrl.read()


class Reassert(unittest.TestCase):
    def make(self, ctrl):
        cam = m.CameraCtrls.__new__(m.CameraCtrls)
        cam.v4l_ctrls = FakeV4l(ctrl)
        cam.device = '/dev/null'
        log = []

        def setup(params, errs):
            for k, v in params.items():
                log.append((k, v))
                if k == 'auto_exposure':
                    ctrl.write(v)
        cam.setup_ctrls = setup
        return cam, log

    def test_cycles_through_aperture_priority_then_writes_mode_and_values(self):
        c = FakeCtrl()
        cam, log = self.make(c)
        sleeps = []
        self.assertTrue(cam.reassert_exposure('manual_mode', {'exposure_time_absolute': '156', 'gain': '0'}, [], sleeps.append))
        self.assertEqual(log, [('auto_exposure', 'aperture_priority_mode'), ('auto_exposure', 'manual_mode'),
                               ('exposure_time_absolute', '156'), ('gain', '0')])
        self.assertEqual(sleeps, [0.3])

    def test_retries_when_the_mode_write_is_ignored_and_rewrites_values(self):
        c = FakeCtrl()
        c.fail_writes = 2
        cam, log = self.make(c)
        errs = []
        self.assertTrue(cam.reassert_exposure('manual_mode', {'gain': '0'}, errs, lambda s: None))
        self.assertEqual([k for k, _ in log].count('gain'), 2)
        self.assertEqual(errs, [])

    def test_reports_when_it_never_sticks(self):
        c = FakeCtrl(stuck='aperture_priority_mode')
        cam, log = self.make(c)
        errs = []
        self.assertFalse(cam.reassert_exposure('manual_mode', {}, errs, lambda s: None))
        self.assertEqual(len(errs), 1)


class ShortStreamTests(unittest.TestCase):
    def run_stream(self, fail_on=None, errno_=errno.EBUSY):
        calls = []

        def fake_ioctl(fd, req, arg=None):
            calls.append(req)
            if req == fail_on:
                raise OSError(errno_, 'boom')
        with mock.patch.object(m, 'ioctl', fake_ioctl), mock.patch.object(m.os, 'open', return_value=99), \
                mock.patch.object(m.os, 'close') as close:
            with m.ShortStream('/dev/video0') as st:
                inside = st.streaming
        return calls, inside, close

    def test_streams_for_the_block_and_lets_go(self):
        calls, inside, close = self.run_stream()
        self.assertTrue(inside)
        self.assertEqual(calls, [m.VIDIOC_REQBUFS, m.VIDIOC_QBUF, m.VIDIOC_QBUF, m.VIDIOC_STREAMON,
                                 m.VIDIOC_STREAMOFF, m.VIDIOC_REQBUFS])
        close.assert_called_once_with(99)

    def test_busy_device_means_someone_else_streams_so_nothing_is_opened(self):
        calls, inside, close = self.run_stream(fail_on=m.VIDIOC_REQBUFS)
        self.assertFalse(inside)
        self.assertEqual(calls, [m.VIDIOC_REQBUFS])
        close.assert_called_once_with(99)

    def test_streamon_failure_still_releases_the_buffers(self):
        calls, inside, close = self.run_stream(fail_on=m.VIDIOC_STREAMON, errno_=errno.EIO)
        self.assertFalse(inside)
        self.assertEqual(calls[-1], m.VIDIOC_REQBUFS)
        close.assert_called_once_with(99)

    def test_unopenable_device_is_not_an_error(self):
        with mock.patch.object(m.os, 'open', side_effect=PermissionError):
            with m.ShortStream('/dev/video0') as st:
                self.assertFalse(st.streaming)


class UsbReset(unittest.TestCase):
    def test_refuses_a_device_that_is_not_the_c60e(self):
        with mock.patch.object(m, 'usb_device_dir', return_value='/nonexistent'):
            ok, msg = m.usb_reset('/dev/video0')
        self.assertFalse(ok)

    def test_no_write_access_names_the_udev_rule(self):
        files = {'idVendor': '328f', 'idProduct': '00e1', 'busnum': '1', 'devnum': '7'}

        def fake_open(path, *a, **k):
            if path == '/dev/bus/usb/001/007':
                raise PermissionError
            raise AssertionError(path)
        with mock.patch.object(m, 'usb_device_dir', return_value='/d'), \
                mock.patch('builtins.open', mock.mock_open()) as mo, mock.patch.object(m.os, 'open', fake_open):
            mo.side_effect = lambda path, *a, **k: mock.mock_open(read_data=files[path.rsplit('/', 1)[1]])()
            ok, msg = m.usb_reset('/dev/video0')
        self.assertFalse(ok)
        self.assertIn('70-emeet-c60e.rules', msg)
        self.assertIn('/dev/bus/usb/001/007', msg)

    def test_resets_with_the_usbdevfs_ioctl(self):
        files = {'idVendor': '328f', 'idProduct': '00e1', 'busnum': '1', 'devnum': '7'}
        calls = []
        with mock.patch.object(m, 'usb_device_dir', return_value='/d'), \
                mock.patch('builtins.open', mock.mock_open()) as mo, mock.patch.object(m.os, 'open', return_value=5), \
                mock.patch.object(m.os, 'close'), mock.patch.object(m, 'ioctl', lambda fd, req, arg=0: calls.append((fd, req))):
            mo.side_effect = lambda path, *a, **k: mock.mock_open(read_data=files[path.rsplit('/', 1)[1]])()
            ok, msg = m.usb_reset('/dev/video0')
        self.assertTrue(ok)
        self.assertEqual(calls, [(5, 0x5514)])


class Luma(unittest.TestCase):
    def test_i420_and_rgb(self):
        self.assertAlmostEqual(emwatch.mean_luma(bytes([16]) * (64 * 48) + bytes(64 * 24), 'I420', 64, 48), 16)
        self.assertAlmostEqual(emwatch.mean_luma(bytes([200, 200, 200, 0]) * (64 * 48), 'BGRx', 64, 48), 200, delta=1)

    def test_bad_input_gives_none(self):
        self.assertIsNone(emwatch.mean_luma(b'', 'I420', 64, 48))
        self.assertIsNone(emwatch.mean_luma(b'x' * 100, 'MJPG', 64, 48))
        self.assertIsNone(emwatch.mean_luma(b'x', 'I420', 0, 0))


class Watchdog(unittest.TestCase):
    def run_seq(self, seq, w=None):
        w = w or emwatch.DarkWatchdog()
        return [t for t, v in seq if w.feed(t, v)]

    def test_fires_after_four_dark_seconds_following_a_bright_scene(self):
        seq = [(0, 90), (2, 90), (4, 16), (6, 16), (8, 16), (10, 16)]
        self.assertEqual(self.run_seq(seq), [8])

    def test_a_dim_room_from_the_start_never_fires(self):
        self.assertEqual(self.run_seq([(t, 16) for t in range(0, 40, 2)]), [])

    def test_a_short_dip_does_not_fire(self):
        self.assertEqual(self.run_seq([(0, 90), (2, 16), (4, 16), (6, 90), (8, 90)]), [])

    def test_gentle_dimming_does_not_fire(self):
        self.assertEqual(self.run_seq([(0, 30), (2, 25), (4, 21), (6, 21), (8, 21), (10, 21)]), [])

    def test_fires_once_then_again_only_after_a_recovery(self):
        w = emwatch.DarkWatchdog()
        seq = [(0, 90), (2, 16), (4, 16), (6, 16), (8, 16), (10, 16), (12, 90), (14, 16), (16, 16), (18, 16)]
        self.assertEqual(self.run_seq(seq, w), [6, 18])

    def test_drop_older_than_a_minute_is_not_a_drop(self):
        self.assertEqual(self.run_seq([(0, 90), (70, 16), (72, 16), (74, 16), (76, 16)]), [])

    def test_none_samples_are_ignored(self):
        w = emwatch.DarkWatchdog()
        self.assertFalse(w.feed(0, None))


if __name__ == '__main__':
    unittest.main()
