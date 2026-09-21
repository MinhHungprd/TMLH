from perf_metrics import PerfMetrics, format_perf_report


def test_perf_metrics_aggregate_and_drain_on_interval():
    times = iter(
        [
            0.0,
            5.0,
            15.0,
        ]
    )

    metrics = PerfMetrics(
        interval=15.0,
        now=times.__next__,
    )

    metrics.record_ms(
        "capture_ms",
        10.0,
    )
    metrics.record_ms(
        "capture_ms",
        30.0,
    )

    assert metrics.drain_if_due() is None

    report = metrics.drain_if_due()

    assert report["capture_ms"] == {
        "count": 2,
        "avg_ms": 20.0,
        "max_ms": 30.0,
    }

    assert (
        "capture_ms=20.0avg/30.0max(n=2)"
        in format_perf_report(report)
    )
