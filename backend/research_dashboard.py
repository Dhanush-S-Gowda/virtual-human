"""Research dashboard queries over individual samples, not revised CV summaries."""

from copy import deepcopy


METRICS = {
    "stt_completion_ms": ("STT completion", "ms"),
    "llm_ttft_ms": ("LLM time to first token", "ms"),
    "tts_first_audio_ms": ("TTS first usable audio", "ms"),
    "object_detection_ms": ("Object detection", "ms"),
    "keypoint_estimation_ms": ("Keypoint estimation", "ms"),
    "object_fps": ("Object detection throughput", "FPS"),
    "keypoint_fps": ("Keypoint estimation throughput", "FPS"),
    "llm_gesture_parse_ms": ("Gesture parser", "ms"),
    "gesture_cue_assignment_ms": ("Gesture cue assignment", "ms"),
    "gesture_audio_alignment_ms": ("Gesture/audio alignment", "ms"),
    "cv_world_state_fetch_ms": ("CV world-state fetch", "ms"),
    "cv_latest_event_age_ms": ("Visual observation age", "ms"),
}


def summary_sql():
    labels = "CASE metric " + " ".join(f"WHEN '{key}' THEN '{label}'" for key, (label, _) in METRICS.items()) + " ELSE metric END"
    return f"""WITH samples AS (
SELECT sample_id, metric, unit, provider, model, experiment_id, host,
       image_width, image_height, people_count, status, max(value) AS value
FROM "vh_performance"
WHERE event = 'measurement' AND phase = 'measured'
AND (metric <> 'stt_completion_ms' OR timing_boundary = 'browser_release_to_final_transcript')
GROUP BY sample_id, metric, unit, provider, model, experiment_id, host,
         image_width, image_height, people_count, status
)
SELECT {labels} AS "Metric", unit AS "Unit", experiment_id AS "Experiment",
       host AS "Host", provider AS "Provider", model AS "Model",
       image_width AS "Width", image_height AS "Height", people_count AS "People",
       count(CASE WHEN status = 'ok' THEN value END) AS "Samples",
       sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS "Errors",
       round(avg(CASE WHEN status = 'ok' THEN value END), 3) AS "Mean",
       round(approx_percentile_cont(CASE WHEN status = 'ok' THEN value END, 0.5), 3) AS "Median (approx)",
       round(approx_percentile_cont(CASE WHEN status = 'ok' THEN value END, 0.95), 3) AS "P95 (approx)",
       round(approx_percentile_cont(CASE WHEN status = 'ok' THEN value END, 0.99), 3) AS "P99 (approx)",
       CASE WHEN count(CASE WHEN status = 'ok' THEN value END) = 0 THEN 'No successful samples'
            WHEN count(CASE WHEN status = 'ok' THEN value END) < 1000 THEN 'Small sample: interpret P99 cautiously'
            ELSE 'Measured' END AS "Sample note"
FROM samples
GROUP BY metric, unit, provider, model, experiment_id, host, image_width, image_height, people_count
ORDER BY "Metric", "Experiment", "Host", "Model"
"""


def research_tab(make_panel):
    summary = make_panel(0, "Performance Summary", "vh_performance", summary_sql())
    summary["id"] = "research-performance-summary"
    summary["layout"].update(x=0, y=0, w=48, h=20)
    summary["description"] = (
        "Raw measured samples in the selected time range; warm-up excluded. Grouped by experiment, host, "
        "model and scene size. Median/P95/P99 are approximate. FPS is successful analyzed frames per "
        "second over ~2-second intervals; no-person landmark polls are excluded. TTS measures usable "
        "audio with timestamps, including scheduling inside the synthesis call, not HTTP first byte. "
        "Gesture rows are computation cost per turn, not observed synchronization error. "
        "STT completion is measured in the browser from release to final transcript receipt; older STT boundaries are excluded. "
        "Use dashboard filters for experiment_id, host, provider, model and scene dimensions. "
        "An empty table means no new measurements have arrived."
    )
    specs = [
        ("Host CPU usage (%)", "vh_resources", "host_cpu_percent", "event = 'host_resource'"),
        ("CPU cores (%)", "vh_resources", "core_cpu_percent", "event = 'host_resource'"),
        ("CPU scheduling pressure (%)", "vh_resources", "cpu_pressure_percent", "event = 'host_resource'"),
        ("Host RAM usage (%)", "vh_resources", "host_ram_percent", "event = 'host_resource'"),
        ("GPU activity (%)", "vh_resources", "gpu_busy_percent", "event = 'gpu_resource' AND resource_scope = 'device'"),
        ("GPU engine activity (%)", "vh_resources", "gpu_engine_percent", "event = 'gpu_resource' AND resource_scope = 'process_gpu'"),
        ("VRAM used (MiB)", "vh_resources", "vram_used_mib", "event = 'gpu_resource' AND resource_scope = 'device'"),
        ("VRAM capacity used (%)", "vh_resources", "vram_used_percent", "event = 'gpu_resource' AND resource_scope = 'device'"),
        ("GPU resident memory (MiB)", "vh_resources", "gpu_resident_mib", "event = 'gpu_resource' AND resource_scope = 'process_gpu'"),
    ]
    panels = [summary]

    def chart(title, stream, sql, description=""):
        item = make_panel(len(panels), title, stream, sql, metric="value")
        item["id"] = "research-" + title.lower().replace(" ", "-").replace("(%)", "percent").replace("(MiB)", "mib")
        index = len(panels) - 1
        item["layout"].update(x=(index % 2) * 24, y=20 + (index // 2) * 12, w=24, h=12)
        item["description"] = description
        panels.append(item)
        return item

    for title, stream, field, predicate in specs:
        sql = (f'SELECT histogram(_timestamp) AS x, concat(host, \' / \', task) AS task, avg({field}) AS y '
               f'FROM "{stream}" WHERE {predicate} AND phase = \'measured\' AND {field} IS NOT NULL GROUP BY x, host, task ORDER BY x')
        item = chart(title, stream, sql)
        if field == "core_cpu_percent":
            item["type"] = "heatmap"
            fields = item["queries"][0]["fields"]
            fields["y"] = [{"label": "CPU", "alias": "task", "column": "task"}]
            fields["z"] = [{"label": "Busy %", "alias": "y", "column": "core_cpu_percent"}]
            fields["breakdown"] = []
        if field == "cpu_pressure_percent":
            item["description"] = "Linux PSI some avg10: share of time runnable tasks waited for CPU, over the preceding 10 seconds. Unavailable on unsupported systems."
        if field.startswith("gpu_") or field.startswith("vram_"):
            item["description"] = "Only reported hardware measurements are plotted. Missing/unsupported values stay empty. Resident system memory is separate from dedicated VRAM."

    chart("VRAM by service (MiB)", "vh_resources", """SELECT x, concat(host, ' / ', task, ' / ', gpu_id) AS task, sum(used) AS y
FROM (SELECT histogram(_timestamp) AS x, host, task, gpu_id, pid, avg(process_vram_mib) AS used
      FROM "vh_resources" WHERE event = 'gpu_resource' AND phase = 'measured' AND resource_scope = 'process_gpu'
      AND process_vram_mib IS NOT NULL GROUP BY x, host, task, gpu_id, pid)
GROUP BY x, host, task, gpu_id ORDER BY x""",
          "Known local service processes and their descendants only. Shared-process models cannot be separated; hosted models are not observable. Process memory can be unavailable under Windows WDDM or container PID isolation.")

    for label in ("object", "keypoint"):
        chart(label.title() + " FPS", "vh_cv", f"""SELECT histogram(_timestamp) AS x, concat(host, ' / ', source_id, ' / Achieved') AS task, avg({label}_fps) AS y
FROM "vh_cv" WHERE event = 'throughput' AND phase = 'measured' GROUP BY x, host, source_id
UNION ALL SELECT histogram(_timestamp) AS x, concat(host, ' / ', source_id, ' / Configured limit') AS task, avg({label}_target_fps) AS y
FROM "vh_cv" WHERE event = 'throughput' AND phase = 'measured' GROUP BY x, host, source_id ORDER BY x""",
              "Successful completions per elapsed interval, including zero when the active camera stalls. Landmark calls without eligible people do not count as analyzed frames.")
        chart(label.title() + " busy skips (%)", "vh_cv",
              f'SELECT histogram(_timestamp) AS x, concat(host, \' / \', source_id) AS task, avg({label}_busy_skip_percent) AS y '
              f'FROM "vh_cv" WHERE event = \'throughput\' AND phase = \'measured\' AND {label}_busy_skip_percent IS NOT NULL GROUP BY x, host, source_id ORDER BY x',
              "Percentage of input frames whose stage was skipped because a previous sample was still running; intentional sampling-limit skips are separate.")
    chart("Visual observation age (ms)", "vh_performance",
          'SELECT histogram(_timestamp) AS x, concat(host, \' / \', experiment_id) AS task, avg(value) AS y '
          'FROM "vh_performance" WHERE event = \'measurement\' AND metric = \'cv_latest_event_age_ms\' '
          'AND phase = \'measured\' AND status = \'ok\' GROUP BY x, host, experiment_id ORDER BY x')
    status = make_panel(len(panels), "GPU status", "vh_resources", """SELECT host, task, gpu_name, gpu_id,
resource_scope, gpu_status, gpu_detail, _timestamp AS last_seen
FROM (SELECT *, row_number() OVER (PARTITION BY host, task, gpu_id, resource_scope ORDER BY _timestamp DESC) AS rn
      FROM "vh_resources" WHERE event = 'gpu_resource')
WHERE rn = 1 ORDER BY last_seen DESC LIMIT 100""")
    status["id"] = "research-gpu-status"
    status["layout"].update(x=0, y=20 + ((len(panels) - 1 + 1) // 2) * 12, w=48, h=10)
    status["description"] = "Availability of GPU statistics. Check last_seen; this is not a claim of zero GPU use."
    panels.append(status)
    return {"tabId": "performance", "name": "Performance", "panels": panels}


def merge_research_dashboard(saved, make_panel):
    """Idempotently replace the managed tab while preserving the user's overview."""
    result = deepcopy(saved)
    tab = research_tab(make_panel)
    result["tabs"] = [item for item in result.get("tabs", []) if item.get("tabId") != tab["tabId"]] + [tab]
    result.setdefault("variables", {"list": []})["showDynamicFilters"] = True
    return result
