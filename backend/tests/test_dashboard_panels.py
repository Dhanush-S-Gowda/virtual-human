from copy import deepcopy

from backend.scripts.setup_observability import dashboards, visible_table_columns


def test_every_log_table_exposes_sql_result_columns():
    dashboard = next(dashboards())
    panels = dashboard["tabs"][0]["panels"]
    assert len(panels) == 13
    for panel in panels:
        if panel["type"] != "table":
            continue
        rows = [{"event": "transcription", "transcript_text": "Hello", "duration_ms": 25.}]
        columns = visible_table_columns(panel, rows)
        assert {"event", "transcript_text", "duration_ms"} <= set(columns), panel["title"]


def test_verification_detects_the_original_empty_table_configuration():
    panel = deepcopy(next(dashboards())["tabs"][0]["panels"][0])
    panel["config"].pop("table_dynamic_columns")
    assert visible_table_columns(panel, [{"event": "frame"}]) == []


def test_metric_axes_remain_mapped_and_dynamic_table_columns_do_not_change_charts():
    for panel in next(dashboards())["tabs"][0]["panels"]:
        if panel["type"] == "line":
            fields = panel["queries"][0]["fields"]
            assert fields["x"] and fields["y"] and fields["breakdown"]
            assert not panel["config"].get("table_dynamic_columns")
