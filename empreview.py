"""Live preview and frame grabbing through GStreamer (python gi), in the app's own process.

The caps follow the camera's current capture format, so this never asks the device for a
different format than OBS or the viewer already has."""

import logging, re, threading, time

import emwatch

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

DEVICE_RE = re.compile(r'^/dev/[A-Za-z0-9_./:@+-]+$')
LUMA_EVERY = 2.0
RAW_FORMATS = {'YUYV': 'YUY2', 'YUY2': 'YUY2', 'NV12': 'NV12', 'UYVY': 'UYVY', 'YU12': 'I420'}


class PreviewUnavailable(Exception):
    """GStreamer or one of its plugins is missing, or the format can't be shown."""


class PreviewBusy(Exception):
    pass


def _make(factory, **props):
    el = Gst.ElementFactory.make(factory, None)
    if el is None:
        raise PreviewUnavailable(f'the GStreamer element {factory} is missing')
    for k, v in props.items():
        el.set_property(k.replace('_', '-'), v)
    return el


def _caps(text):
    return Gst.Caps.from_string(text)


def _fraction(fps):
    num, den = Gst.util_double_to_fraction(float(fps))
    return f'{num}/{den}'


def source_chain(device, fmt):
    """Elements from v4l2src to decoded raw video. fmt: pixelformat, width, height, fps."""
    Gst.init(None)
    if not DEVICE_RE.match(device or ''):
        raise PreviewUnavailable(f'not a device path: {device!r}')
    pxf, w, h, fps = fmt['pixelformat'], int(fmt['width']), int(fmt['height']), fmt['fps']
    size = f'width={w},height={h},framerate={_fraction(fps)}'
    chain = [_make('v4l2src', device=device)]
    if pxf == 'MJPG':
        chain += [_make('capsfilter', caps=_caps(f'image/jpeg,{size}')), _make('jpegdec')]
    elif pxf in RAW_FORMATS:
        chain += [_make('capsfilter', caps=_caps(f'video/x-raw,format={RAW_FORMATS[pxf]},{size}'))]
    else:
        raise PreviewUnavailable(f'the {pxf} capture format can\'t be shown here, use the viewer button')
    chain.append(_make('videoconvert'))
    return chain


def _link(pipeline, elements):
    for el in elements:
        pipeline.add(el)
    for a, b in zip(elements, elements[1:]):
        if not a.link(b):
            raise PreviewUnavailable(f'GStreamer could not link {a.get_name()} to {b.get_name()}')


def _is_busy(err, debug):
    text = f'{err.message} {debug or ""}'.lower()
    return err.matches(Gst.ResourceError.quark(), Gst.ResourceError.BUSY) or 'busy' in text or 'ebusy' in text


class Preview:
    """v4l2src ! decode ! videoconvert ! gtk4paintablesink. on_error(busy: bool, message) runs in the main loop."""

    def __init__(self, device, fmt, on_error):
        self.device, self.fmt, self.on_error = device, fmt, on_error
        self.pipeline = None
        self.bus = None
        self.sink_pad = None
        self.frames = 0
        self._probe = None
        self._fps_at = (time.monotonic(), 0)
        self.fps = 0.0
        self.luma = None
        self.luma_at = 0.0
        self._luma_pad = self._luma_probe = None

    def start(self):
        """Starts streaming and returns the Gdk.Paintable that shows it."""
        sink = _make('gtk4paintablesink')
        chain = source_chain(self.device, self.fmt)
        self.pipeline = Gst.Pipeline.new('preview')
        _link(self.pipeline, chain + [sink])
        self.sink_pad = sink.get_static_pad('sink')
        self._probe = self.sink_pad.add_probe(Gst.PadProbeType.BUFFER, self._count)
        self._luma_pad = chain[-1].get_static_pad('src')
        self._luma_probe = self._luma_pad.add_probe(Gst.PadProbeType.BUFFER, self._sample_luma)
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message::error', self._error)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._take_error()
        return sink.get_property('paintable')

    def _count(self, pad, info):
        self.frames += 1
        return Gst.PadProbeReturn.OK

    def _sample_luma(self, pad, info):
        now = time.monotonic()
        if now - self.luma_at < LUMA_EVERY:
            return Gst.PadProbeReturn.OK
        self.luma_at = now
        caps = pad.get_current_caps()
        ok, mapped = info.get_buffer().map(Gst.MapFlags.READ)
        if caps is None or not ok:
            return Gst.PadProbeReturn.OK
        try:
            s = caps.get_structure(0)
            self.luma = emwatch.mean_luma(mapped.data, s.get_string('format'), s.get_value('width'), s.get_value('height'))
        finally:
            info.get_buffer().unmap(mapped)
        return Gst.PadProbeReturn.OK

    def _take_error(self):
        msg = self.bus.pop_filtered(Gst.MessageType.ERROR)
        if msg is None:
            self.stop()
            raise PreviewUnavailable('GStreamer could not start the preview')
        err, debug = msg.parse_error()
        self.stop()
        if _is_busy(err, debug):
            raise PreviewBusy(err.message)
        raise PreviewUnavailable(err.message)

    def _error(self, bus, msg):
        err, debug = msg.parse_error()
        logging.info(f'preview error: {err.message} ({debug})')
        busy = _is_busy(err, debug)
        self.stop()
        self.on_error(busy, err.message)

    def size(self):
        caps = self.sink_pad.get_current_caps() if self.sink_pad else None
        if caps is None or caps.get_size() == 0:
            return None
        s = caps.get_structure(0)
        ok_w, w = s.get_int('width')
        ok_h, h = s.get_int('height')
        return (w, h) if ok_w and ok_h else None

    def measure_fps(self):
        now = time.monotonic()
        t0, f0 = self._fps_at
        if now - t0 >= 0.5:
            self.fps = (self.frames - f0) / (now - t0)
            self._fps_at = (now, self.frames)
        return self.fps

    def stop(self):
        """Releases the camera."""
        pipeline, self.pipeline = self.pipeline, None
        if pipeline is None:
            return
        if self.sink_pad is not None and self._probe is not None:
            self.sink_pad.remove_probe(self._probe)
        if self._luma_pad is not None and self._luma_probe is not None:
            self._luma_pad.remove_probe(self._luma_probe)
        self.sink_pad = self._probe = self._luma_pad = self._luma_probe = None
        self.luma = None
        if self.bus is not None:
            self.bus.remove_signal_watch()
            self.bus = None
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_state(2 * Gst.SECOND)


class GstGrabber:
    """Keeps the camera open and hands out the newest frame as 320x180 RGB."""

    def __init__(self, device, fmt):
        from emcheck import Frame, GrabError, GRAB_W, GRAB_H
        self._Frame, self._GrabError, self.w, self.h = Frame, GrabError, GRAB_W, GRAB_H
        Gst.init(None)
        chain = source_chain(device, fmt)
        chain += [_make('videoscale'), _make('capsfilter', caps=_caps(f'video/x-raw,format=RGB,width={GRAB_W},height={GRAB_H}')),
                  _make('appsink', max_buffers=1, drop=True, sync=False)]
        self.sink = chain[-1]
        self.pipeline = Gst.Pipeline.new('grab')
        _link(self.pipeline, chain)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.bus = self.pipeline.get_bus()
        try:
            self.grab()
        except GrabError:
            self.close()
            raise

    def grab(self, timeout=8.0):
        sample = self.sink.emit('try-pull-sample', int(timeout * Gst.SECOND))
        if sample is None:
            msg = self.bus.pop_filtered(Gst.MessageType.ERROR)
            if msg is not None:
                err, debug = msg.parse_error()
                raise self._GrabError(('The camera is busy. ' if _is_busy(err, debug) else '') + err.message)
            raise self._GrabError('the camera gave no frame')
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise self._GrabError('could not read a frame')
        try:
            return self._Frame(self.w, self.h, bytes(info.data)[:self.w * self.h * 3])
        finally:
            buf.unmap(info)

    def close(self):
        pipeline, self.pipeline = self.pipeline, None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(2 * Gst.SECOND)
