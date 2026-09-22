"""Low-impact on-demand batched screen capture.

The broker is deliberately NOT a video/continuous capture loop. Every game
worker requests a frame only when its existing state machine needs one.

All capture requests share one MSS worker thread. Requests arriving inside a
small smoothing window are merged into one grab per monitor and then cropped
back into independent grayscale NumPy arrays. This trades a little latency
for fewer capture bursts, lower contention, and smoother multi-profile runs.
"""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time

import cv2
import numpy as np

from perf_metrics import record_perf_ms


CAPTURE_COALESCE_SECONDS = 0.12
CAPTURE_TIMEOUT_SECONDS = 2.0


@dataclass
class _CaptureRequest:
    rect: tuple[int, int, int, int]
    done: threading.Event
    result: np.ndarray | None = None
    error: BaseException | None = None


class BossCaptureBroker:
    def __init__(
        self,
        *,
        coalesce_seconds: float = CAPTURE_COALESCE_SECONDS,
        timeout_seconds: float = CAPTURE_TIMEOUT_SECONDS,
        grabber_factory=None,
        monitor_key=None,
    ):
        self.coalesce_seconds = max(
            0.0,
            float(coalesce_seconds),
        )
        self.timeout_seconds = max(
            0.05,
            float(timeout_seconds),
        )
        self._grabber_factory = (
            grabber_factory
            or self._default_grabber_factory
        )
        self._monitor_key = (
            monitor_key
            or self._default_monitor_key
        )

        self._queue = queue.Queue()
        self._thread = None
        self._thread_lock = threading.Lock()
        self._closed = False
        self._disabled_error = None

    @staticmethod
    def _default_grabber_factory():
        import mss

        return mss.mss()

    @staticmethod
    def _default_monitor_key(
        rect: tuple[int, int, int, int],
    ):
        import win32api
        import win32con

        left, top, width, height = rect
        center = (
            left + width // 2,
            top + height // 2,
        )

        return win32api.MonitorFromPoint(
            center,
            win32con.MONITOR_DEFAULTTONEAREST,
        )

    def _ensure_thread(self) -> None:
        if self._closed:
            raise RuntimeError(
                "Boss capture broker is closed"
            )

        if self._disabled_error is not None:
            raise RuntimeError(
                "MSS screen capture is unavailable"
            ) from self._disabled_error

        thread = self._thread

        if (
            thread is not None
            and thread.is_alive()
        ):
            return

        with self._thread_lock:
            thread = self._thread

            if (
                thread is not None
                and thread.is_alive()
            ):
                return

            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="screen-capture-broker",
            )
            self._thread.start()

    def capture_rect(
        self,
        rect: tuple[int, int, int, int],
    ) -> np.ndarray:
        left, top, width, height = (
            int(value)
            for value in rect
        )

        if width <= 0 or height <= 0:
            raise ValueError(
                f"Invalid capture rect: {rect!r}"
            )

        self._ensure_thread()

        request = _CaptureRequest(
            rect=(
                left,
                top,
                width,
                height,
            ),
            done=threading.Event(),
        )

        self._queue.put(request)

        if not request.done.wait(
            self.timeout_seconds
        ):
            raise TimeoutError(
                "MSS screen capture timed out"
            )

        if request.error is not None:
            raise RuntimeError(
                "MSS screen capture failed"
            ) from request.error

        if request.result is None:
            raise RuntimeError(
                "MSS screen capture returned no frame"
            )

        return request.result

    def close(self) -> None:
        self._closed = True
        self._queue.put(None)

        thread = self._thread

        if thread is not None:
            thread.join(
                timeout=0.5,
            )

    def _run(self) -> None:
        try:
            grabber = self._grabber_factory()
        except BaseException as exc:
            self._disabled_error = exc
            self._fail_pending(exc)
            return

        try:
            while not self._closed:
                first = self._queue.get()

                if first is None:
                    return

                batch = [first]

                deadline = (
                    time.perf_counter()
                    + self.coalesce_seconds
                )

                while True:
                    remaining = (
                        deadline
                        - time.perf_counter()
                    )

                    if remaining <= 0:
                        break

                    try:
                        item = self._queue.get(
                            timeout=remaining
                        )
                    except queue.Empty:
                        break

                    if item is None:
                        self._closed = True
                        break

                    batch.append(item)

                try:
                    self._capture_batch(
                        grabber,
                        batch,
                    )
                except BaseException as exc:
                    for request in batch:
                        request.error = exc
                finally:
                    for request in batch:
                        request.done.set()

                if self._closed:
                    return
        finally:
            close = getattr(
                grabber,
                "close",
                None,
            )

            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _fail_pending(
        self,
        error: BaseException,
    ) -> None:
        while True:
            try:
                request = (
                    self._queue.get_nowait()
                )
            except queue.Empty:
                return

            if request is None:
                continue

            request.error = error
            request.done.set()

    def _capture_batch(
        self,
        grabber,
        batch,
    ) -> None:
        groups = {}

        for request in batch:
            key = self._monitor_key(
                request.rect
            )
            groups.setdefault(
                key,
                [],
            ).append(request)

        started = time.perf_counter()

        for requests in groups.values():
            self._capture_group(
                grabber,
                requests,
            )

        record_perf_ms(
            "capture_batch_ms",
            (
                time.perf_counter()
                - started
            )
            * 1000.0,
        )
        record_perf_ms(
            "capture_batch_size",
            float(len(batch)),
        )

    @staticmethod
    def _capture_group(
        grabber,
        requests,
    ) -> None:
        left = min(
            request.rect[0]
            for request in requests
        )
        top = min(
            request.rect[1]
            for request in requests
        )
        right = max(
            request.rect[0]
            + request.rect[2]
            for request in requests
        )
        bottom = max(
            request.rect[1]
            + request.rect[3]
            for request in requests
        )

        width = max(
            1,
            right - left,
        )
        height = max(
            1,
            bottom - top,
        )

        shot = grabber.grab(
            {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
        )

        frame = np.asarray(
            shot
        )

        if (
            frame.ndim != 3
            or frame.shape[0] < height
            or frame.shape[1] < width
            or frame.shape[2] < 3
        ):
            raise RuntimeError(
                "Unexpected MSS frame shape: "
                f"{frame.shape!r}"
            )

        for request in requests:
            req_left, req_top, req_width, req_height = (
                request.rect
            )

            x = req_left - left
            y = req_top - top

            region = frame[
                y:y + req_height,
                x:x + req_width,
            ]

            if (
                region.shape[0] != req_height
                or region.shape[1] != req_width
            ):
                raise RuntimeError(
                    "MSS crop is outside captured batch"
                )

            if region.shape[2] >= 4:
                gray = cv2.cvtColor(
                    region,
                    cv2.COLOR_BGRA2GRAY,
                )
            else:
                gray = cv2.cvtColor(
                    region,
                    cv2.COLOR_BGR2GRAY,
                )

            request.result = gray


SCREEN_CAPTURE_BROKER = BossCaptureBroker()

# Backward-compatible alias for older imports.
BOSS_CAPTURE_BROKER = SCREEN_CAPTURE_BROKER
