import threading
import time

import numpy as np

from capture_broker import BossCaptureBroker


class FakeGrabber:
    def __init__(self):
        self.calls = []
        self.closed = False

    def grab(self, monitor):
        self.calls.append(
            dict(monitor)
        )

        height = monitor["height"]
        width = monitor["width"]

        frame = np.zeros(
            (height, width, 4),
            dtype=np.uint8,
        )

        # Deterministic BGRA data. Grayscale output is not important here;
        # shape and batching behavior are.
        frame[:, :, 0] = 20
        frame[:, :, 1] = 40
        frame[:, :, 2] = 60
        frame[:, :, 3] = 255

        return frame

    def close(self):
        self.closed = True


def test_two_near_simultaneous_requests_use_one_grab():
    fake = FakeGrabber()

    broker = BossCaptureBroker(
        coalesce_seconds=0.05,
        timeout_seconds=1.0,
        grabber_factory=lambda: fake,
        monitor_key=lambda rect: "monitor-1",
    )

    results = {}
    start = threading.Event()

    def capture(name, rect):
        start.wait()
        results[name] = broker.capture_rect(
            rect
        )

    first = threading.Thread(
        target=capture,
        args=("a", (100, 100, 20, 10)),
    )
    second = threading.Thread(
        target=capture,
        args=("b", (130, 120, 15, 8)),
    )

    first.start()
    second.start()
    start.set()

    first.join(1.0)
    second.join(1.0)

    broker.close()

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(fake.calls) == 1

    assert results["a"].shape == (
        10,
        20,
    )
    assert results["b"].shape == (
        8,
        15,
    )

    assert fake.calls[0] == {
        "left": 100,
        "top": 100,
        "width": 45,
        "height": 28,
    }


def test_capture_is_on_demand_not_continuous():
    fake = FakeGrabber()

    broker = BossCaptureBroker(
        coalesce_seconds=0.0,
        timeout_seconds=1.0,
        grabber_factory=lambda: fake,
        monitor_key=lambda rect: "monitor-1",
    )

    time.sleep(0.03)

    # Merely creating the broker must not capture anything.
    assert fake.calls == []

    image = broker.capture_rect(
        (10, 20, 6, 4)
    )

    time.sleep(0.03)
    broker.close()

    assert image.shape == (
        4,
        6,
    )
    assert len(fake.calls) == 1
