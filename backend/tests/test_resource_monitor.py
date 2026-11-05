"""Resource aggregation is bounded and preserves per-process CPU semantics."""

from backend.scripts.monitor_resources import accumulate, summarize


def sample(cpu, rss, thread_cpu, core):
    return {"role": "cv", "pid": 42, "cpu_percent": cpu, "rss_mib": rss,
            "thread_count": 8, "allowed_logical_cpus": [0, 1, 2, 3],
            "threads": [{"cpu_percent": thread_cpu, "last_logical_cpu": core}]}


def test_streaming_summary_tracks_peaks_and_only_active_sampled_cores():
    totals = {}
    accumulate(totals, sample(120., 100., 80., 2))
    accumulate(totals, sample(20., 90., 0., 3))
    report = summarize(totals)[0]
    assert report["cpu_percent_mean"] == 70.
    assert report["cpu_percent_peak"] == 120.
    assert report["rss_mib_peak"] == 100.
    assert report["observed_active_logical_cpus"] == [2]
    assert report["allowed_logical_cpus"] == [0, 1, 2, 3]
    assert report["thread_count_peak"] == 8
    assert len(totals) == 1
    assert summarize(totals) == summarize(totals)  # Does not consume aggregate state.
