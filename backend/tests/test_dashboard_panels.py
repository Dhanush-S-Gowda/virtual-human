from copy import deepcopy

from backend.scripts.setup_observability import dashboards, visible_table_columns
from backend.scripts.setup_observability import panel
from backend.research_dashboard import merge_research_dashboard, METRICS


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
    for panel in [item for tab in next(dashboards())["tabs"] for item in tab["panels"]]:
        if panel["type"] == "line":
            fields = panel["queries"][0]["fields"]
            assert fields["x"] and fields["y"] and fields["breakdown"]
            assert not panel["config"].get("table_dynamic_columns")


def test_research_update_is_idempotent_and_preserves_custom_overview_and_variables():
    saved = {"title": "Virtual Human", "dashboardId": "test", "tabs": [
        {"tabId": "main", "name": "Overview", "panels": [{"title": "User tile", "id": "custom"}]}],
        "variables": {"list": [{"name": "existing"}]}}
    before = deepcopy(saved)
    updated = merge_research_dashboard(saved, panel)
    assert saved == before
    assert updated["tabs"][0] == before["tabs"][0]
    assert updated["variables"]["list"] == before["variables"]["list"]
    assert merge_research_dashboard(updated, panel) == updated
    research = updated["tabs"][1]
    summary = research["panels"][0]
    assert summary["title"] == "Performance Summary"
    assert summary["layout"]["w"] == 48
    query = summary["queries"][0]["query"]
    assert all(key in query for key in METRICS)
    assert "sample_id" in query and "max(value)" in query
    assert "phase = 'measured'" in query
    assert '"Errors"' in query and "approx_percentile_cont" in query


def test_research_panels_have_distinct_ids_and_nonoverlapping_layout():
    panels = next(dashboards())["tabs"][1]["panels"]
    assert len({item["id"] for item in panels}) == len(panels)
    for index, item in enumerate(panels):
        a = item["layout"]
        for other in panels[index + 1:]:
            b = other["layout"]
            assert (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                    or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])
