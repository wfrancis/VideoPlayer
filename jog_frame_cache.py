"""Keep a bounded run of native video frames ready for the jog dial."""

import bisect
import ctypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtMultimedia import QVideoFrame, QVideoFrameFormat


MAX_FRAMES = 48
MAX_CACHE_BYTES = 420 * 1024 * 1024
DECODE_TIMEOUT_S = 5
PTS_TOLERANCE_US = 1000
TIME_BASE = re.compile(rb"config in time_base:\s*(\d+)/(\d+)")
FRAME_INFO = re.compile(rb"\bn:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:\S+(?:\s+duration:\s*(\d+))?")
FRAME_SIZE = re.compile(rb"\bs:(\d+)x(\d+)\b")
PIXEL_FORMATS = {
    QVideoFrameFormat.PixelFormat.Format_NV12: "nv12",
    QVideoFrameFormat.PixelFormat.Format_YUV420P: "yuv420p",
}


def _find_ffmpeg():
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).resolve().parent.parent / "Resources" / "ffmpeg" / "bin" / "ffmpeg"
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return str(bundled)
    if sys.platform == "darwin":
        # Finder doesn't include Homebrew in its default PATH.
        for path in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return shutil.which("ffmpeg")


@dataclass(frozen=True)
class DecodeWindow:
    start: int
    span: int
    count: int

    @property
    def end(self):
        return self.start + self.span * self.count


class JogFrameCache(QObject):
    _worker_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ffmpeg = _find_ffmpeg()
        self._path = None
        self._format = None
        self._focus = 0
        self._frame_bytes = 0
        self._limit = 0
        self._frames = {}
        self._starts = []
        self._active = None
        self._pending = None
        self._last_attempt = None
        self._failed = False
        self._lock = threading.RLock()
        self._generation = 0
        self._worker = None
        self._process = None
        self._worker_done.connect(self._start_pending)

    def request(self, path, reference, duration_ms, direction=0):
        """Prefetch a window around a displayed frame without blocking Qt."""
        with self._lock:
            self._request(path, reference, duration_ms, direction)

    def _request(self, path, reference, duration_ms, direction):
        if not self._ffmpeg or not path or not Path(path).is_absolute() or not reference.isValid():
            return
        surface = reference.surfaceFormat()
        width, height = reference.width(), reference.height()
        position = reference.startTime()
        span = reference.endTime() - position
        if (
            surface.pixelFormat() not in PIXEL_FORMATS
            or min(width, height, duration_ms) <= 0
            or width % 2 or height % 2
            or position < 0 or span < 2000
        ):
            return
        if path != self._path or surface != self._format:
            self.clear()
            self._path = path
            self._format = surface
            self._frame_bytes = width * height * 3 // 2
            self._limit = min(MAX_FRAMES, MAX_CACHE_BYTES // self._frame_bytes)
        if self._failed or self._limit < 5:
            return

        bias = direction * (self._limit // 5)
        # Fetch further ahead, but retain enough behind for an abrupt turn
        # in the other direction.
        self._focus = position + direction * (self._limit // 9) * span
        index = self._find(reference)
        margin = min(20, self._limit * 5 // 12)
        if index is not None and (
            (direction > 0 or index >= margin or self._starts[0] < span)
            and (direction < 0 or len(self._starts) - index > margin
                 or self._starts[-1] + 2 * span >= duration_ms * 1000)
        ):
            return

        before = self._limit // 2 - bias
        duration = duration_ms * 1000
        start = max(0, min(position - before * span, duration - (self._limit - 2) * span))
        window = DecodeWindow(start, span, self._limit)
        if self._active is not None and self._active.start <= position <= self._active.end:
            if self._active.start <= max(0, position - margin * span * (direction <= 0)) and (
                self._active.end >= min(duration, position + margin * span * (direction >= 0))
            ):
                return
        # Avoid retrying a range where FFmpeg already ran but supplied no
        # usable frames, for example when the track stops before the container.
        if self._last_attempt is not None and abs(self._last_attempt.start - start) < span:
            return
        self._pending = window
        self._start_pending()

    def contains(self, reference):
        with self._lock:
            return self._find(reference) is not None

    def step(self, reference, steps):
        """Return the exact neighbor by decoded frame order, if cached."""
        with self._lock:
            result = self._cached_step(reference, steps)
        if result is None:
            return None
        surface, stamp, end_stamp, pixels = result
        frame = QVideoFrame(surface)
        if not frame.map(QVideoFrame.MapMode.WriteOnly):
            return None
        try:
            self._copy_pixels(frame, pixels)
        finally:
            frame.unmap()
        frame.setStartTime(stamp)
        frame.setEndTime(end_stamp)
        frame.setRotation(reference.rotation())
        frame.setMirrored(reference.mirrored())
        return frame

    def _cached_step(self, reference, steps):
        index = self._find(reference)
        if index is None:
            return None
        target = index + steps
        if not 0 <= target < len(self._starts):
            return None
        # An old and a new window may briefly have a gap on a seek. Don't
        # treat separated frames as adjacent.
        start, end = sorted((index, target))
        if any(
            self._starts[i + 1] - (self._starts[i] + self._frames[self._starts[i]][0]) > PTS_TOLERANCE_US
            for i in range(start, end)
        ):
            return None
        stamp = self._starts[target]
        duration, pixels = self._frames[stamp]
        end_stamp = stamp + duration
        if target + 1 < len(self._starts):
            next_stamp = self._starts[target + 1]
            if next_stamp - end_stamp <= PTS_TOLERANCE_US:
                end_stamp = next_stamp
        return self._format, stamp, end_stamp, pixels

    def clear(self):
        with self._lock:
            self._generation += 1
            self._pending = None
            self._active = None
            self._last_attempt = None
            self._failed = False
            self._path = None
            self._format = None
            self._frames.clear()
            self._starts.clear()
            if self._process is not None:
                self._process.kill()

    def _find(self, reference):
        if not self._starts or not reference.isValid() or reference.surfaceFormat() != self._format:
            return None
        stamp = reference.startTime()
        index = bisect.bisect_left(self._starts, stamp)
        for candidate in (index, index - 1):
            if 0 <= candidate < len(self._starts) and abs(self._starts[candidate] - stamp) <= PTS_TOLERANCE_US:
                return candidate
        return None

    def _copy_pixels(self, frame, pixels):
        width, height = frame.width(), frame.height()
        if frame.pixelFormat() == QVideoFrameFormat.PixelFormat.Format_NV12:
            planes = ((width, height), (width, height // 2))
        else:
            planes = ((width, height), (width // 2, height // 2), (width // 2, height // 2))
        offset = 0
        for plane, (row_bytes, rows) in enumerate(planes):
            dst = int(frame.bits(plane))
            stride = frame.bytesPerLine(plane)
            size = row_bytes * rows
            if stride == row_bytes:
                ctypes.memmove(dst, pixels[offset:offset + size], size)
            else:
                for row in range(rows):
                    begin = offset + row * row_bytes
                    ctypes.memmove(dst + row * stride, pixels[begin:begin + row_bytes], row_bytes)
            offset += size

    def _start_pending(self):
        with self._lock:
            if self._pending is None or self._worker is not None:
                return
            window, self._pending = self._pending, None
            self._active = self._last_attempt = window
            size = self._format.frameSize()
            self._worker = threading.Thread(
                target=self._decode,
                args=(self._generation, self._command(window), window,
                      self._frame_bytes, (size.width(), size.height())),
                daemon=True,
            )
            self._worker.start()

    def _command(self, window):
        fmt = PIXEL_FORMATS[self._format.pixelFormat()]
        color_range = self._format.colorRange()
        if color_range == QVideoFrameFormat.ColorRange.ColorRange_Full:
            # Full-range planar YUV can be copied directly. NV12 needs only
            # its chroma planes interleaved, with the original range retained.
            if fmt == "yuv420p":
                fmt = "yuvj420p"
            conversion = f"scale=out_range=pc,format={fmt},"
        elif color_range == QVideoFrameFormat.ColorRange.ColorRange_Video:
            conversion = f"scale=out_range=tv,format={fmt},"
        else:
            conversion = f"format={fmt},"
        return [
            self._ffmpeg,
            "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info",
            "-filter_threads", "1", "-threads", str(min(8, os.cpu_count() or 1)),
            "-protocol_whitelist", "file,pipe", "-noautorotate",
            "-ss", f"{window.start / 1_000_000:.6f}",
            "-copyts", "-start_at_zero", "-i", self._path,
            "-map", "0:V:0", "-an", "-sn", "-dn", "-fps_mode", "passthrough",
            "-frames:v", str(window.count),
            "-vf", conversion + "showinfo=checksum=0",
            "-pix_fmt", fmt, "-c:v", "rawvideo", "-threads", "1",
            "-f", "rawvideo", "pipe:1",
        ]

    def _decode(self, generation, command, window, frame_bytes, frame_size):
        received = 0
        try:
            with self._lock:
                if self._generation != generation:
                    return
                self._process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                process = self._process
            timer = threading.Timer(DECODE_TIMEOUT_S, process.kill)
            timer.daemon = True
            timer.start()
            timings = queue.SimpleQueue()
            metadata = threading.Thread(
                target=self._read_timings, args=(process.stderr, window, frame_size, timings), daemon=True,
            )
            metadata.start()
            try:
                while received < window.count:
                    pixels = process.stdout.read(frame_bytes)
                    if len(pixels) != frame_bytes:
                        break
                    timing = timings.get(timeout=DECODE_TIMEOUT_S)
                    if timing is None:
                        break
                    with self._lock:
                        if self._generation != generation:
                            break
                        self._store_frame(*timing, pixels)
                    received += 1
            finally:
                process.kill()
                timer.cancel()
                process.wait()
                metadata.join()
                process.stdout.close()
                process.stderr.close()
        except (OSError, queue.Empty):
            pass
        finally:
            with self._lock:
                self._worker = None
                self._process = None
                if self._generation == generation:
                    self._active = None
                    if not received and not self._starts:
                        self._failed = True
                        self._pending = None
            try:
                self._worker_done.emit()
            except RuntimeError:
                # Qt may already have destroyed this object during shutdown.
                pass

    @staticmethod
    def _read_timings(stderr, window, frame_size, timings):
        time_base = None
        for line in stderr:
            if b"Parsed_showinfo" not in line:
                continue
            metadata = TIME_BASE.search(line)
            if metadata is not None and int(metadata[2]) > 0:
                time_base = int(metadata[1]) * 1_000_000, int(metadata[2])
            timing = FRAME_INFO.search(line)
            if timing is not None and time_base is not None:
                size = FRAME_SIZE.search(line)
                if size is None or (int(size[1]), int(size[2])) != frame_size:
                    break
                numerator, denominator = time_base
                stamp = (int(timing[1]) * numerator + denominator // 2) // denominator
                duration = window.span
                if timing[2] is not None and int(timing[2]) > 0:
                    duration = (int(timing[2]) * numerator + denominator // 2) // denominator
                timings.put((stamp, duration))
        timings.put(None)

    def _store_frame(self, stamp, duration, pixels):
        if stamp < 0 or stamp in self._frames:
            return
        if len(self._starts) == self._limit:
            oldest, newest = self._starts[0], self._starts[-1]
            farthest = max((oldest, newest), key=lambda point: abs(point - self._focus))
            if abs(stamp - self._focus) >= abs(farthest - self._focus):
                return
            self._starts.remove(farthest)
            del self._frames[farthest]
        bisect.insort(self._starts, stamp)
        self._frames[stamp] = (duration, pixels)
