"""Low-overhead runtime performance aggregation.

The profiler keeps only count/total/max values in memory and emits one global
summary roughly every 15 seconds when a worker asks for it. It does not write
files and does not change automation timing decisions.
"""

from __future__ import annotations

from contextlib import contextmanager
import threading
import time


PERF_REPORT_INTERVAL = 15.0


class PerfMetrics:
    def __init__(
        self,
        interval: float = PERF_REPORT_INTERVAL,
        now=None,
    ):
        self.interval = float(interval)
        self.now = now or time.monotonic
        self._lock = threading.Lock()
        self._values = {}
        self._last_report = self.now()

    def record_ms(
        self,
        name: str,
        milliseconds: float,
    ) -> None:
        value = max(
            0.0,
            float(milliseconds),
        )

        with self._lock:
            item = self._values.get(name)

            if item is None:
                self._values[name] = [
                    1,
                    value,
                    value,
                ]
                return

            item[0] += 1
            item[1] += value
            item[2] = max(
                item[2],
                value,
            )

    def drain_if_due(self):
        current = self.now()

        with self._lock:
            if (
                current
                - self._last_report
                < self.interval
            ):
                return None

            self._last_report = current

            if not self._values:
                return None

            values = self._values
            self._values = {}

        return {
            name: {
                "count": count,
                "avg_ms": total / count,
                "max_ms": maximum,
            }
            for name, (
                count,
                total,
                maximum,
            ) in values.items()
        }


PERF_METRICS = PerfMetrics()


def record_perf_ms(
    name: str,
    milliseconds: float,
) -> None:
    PERF_METRICS.record_ms(
        name,
        milliseconds,
    )


@contextmanager
def perf_timer(name: str):
    started = time.perf_counter()

    try:
        yield
    finally:
        record_perf_ms(
            name,
            (
                time.perf_counter()
                - started
            )
            * 1000.0,
        )


def format_perf_report(
    report,
) -> str:
    if not report:
        return ""

    preferred = (
        "capture_ms",
        "boss_batch_ms",
        "boss_batch_size",
        "asset_scan_ms",
        "vision_ms",
        "wait_ocr_ms",
        "ocr_ms",
        "frida_ms",
        "debug_io_ms",
    )

    names = [
        name
        for name in preferred
        if name in report
    ]

    names.extend(
        sorted(
            name
            for name in report
            if name not in names
        )
    )

    parts = []

    for name in names:
        item = report[name]
        parts.append(
            (
                f"{name}="
                f"{item['avg_ms']:.1f}avg/"
                f"{item['max_ms']:.1f}max"
                f"(n={item['count']})"
            )
        )

    return "PERF 15s | " + " | ".join(parts)
