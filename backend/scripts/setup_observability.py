"""Provision OpenObserve streams/dashboards; optionally migrate legacy log files."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from observability import STREAMS, credentials, redact, emit, shutdown, resource_sample
from research_dashboard import merge_research_dashboard
import psutil
import os


def panel(index, title, stream, sql, *, metric=None):
    axes = {"stream": stream, "stream_type": "logs", "x": [], "y": [], "z": [],
            "breakdown": [], "filter": {"filterType": "group", "logicalOperator": "AND", "conditions": []}}
    if metric:
        axes["x"] = [{"label": "Time", "alias": "x", "column": "_timestamp"}]
        axes["y"] = [{"label": metric, "alias": "y", "column": metric}]
        axes["breakdown"] = [{"label": "Task", "alias": "task", "column": "task"}]
    return {"id": f"panel-{index}", "type": "line" if metric else "table", "title": title,
            "description": "", "queryType": "sql", "config": {
                "show_legends": True, **({"table_dynamic_columns": True} if not metric else {})},
            "layout": {"x": (index % 2) * 24, "y": (index // 2) * 12, "w": 24, "h": 12, "i": index},
            "queries": [{"customQuery": True, "query": sql, "config": {"promql_legend": ""}, "fields": axes}]}


def dashboards():
    specs = [
        ("Virtual Human - CV", "Camera events, windows and CV execution spans", [
            ("CV events", "vh_cv", 'SELECT * FROM "vh_cv" ORDER BY _timestamp DESC LIMIT 100'),
            ("CV inference latency", "vh_stages", 'SELECT stage, avg(duration_ms) AS mean_ms, max(duration_ms) AS max_ms, count(*) AS calls FROM "vh_stages" WHERE event = \'end\' AND stage LIKE \'cv.%\' GROUP BY stage'),
        ]),
        ("Virtual Human - Conversation Pipeline", "STT, LLM and TTS with correlated turn/session IDs", [
            ("Conversation events", "vh_conversation", 'SELECT * FROM "vh_conversation" ORDER BY _timestamp DESC LIMIT 100'),
            ("Stage latency", "vh_stages", 'SELECT stage, avg(duration_ms) AS mean_ms, max(duration_ms) AS max_ms, count(*) AS calls FROM "vh_stages" WHERE event = \'end\' GROUP BY stage'),
            ("Execution spans", "vh_stages", 'SELECT * FROM "vh_stages" ORDER BY _timestamp DESC LIMIT 100'),
        ]),
        ("Virtual Human - LLM", "Real model requests, replies and timing, without credentials", [
            ("LLM request and response events", "vh_llm", 'SELECT * FROM "vh_llm" ORDER BY _timestamp DESC LIMIT 100'),
        ]),
        ("Virtual Human - STT", "Transcripts and speech recognition latency", [
            ("STT events", "vh_stt", 'SELECT * FROM "vh_stt" ORDER BY _timestamp DESC LIMIT 100'),
        ]),
        ("Virtual Human - TTS", "Speech synthesis, gesture cue timing and failures", [
            ("TTS events", "vh_tts", 'SELECT * FROM "vh_tts" ORDER BY _timestamp DESC LIMIT 100'),
        ]),
        ("Virtual Human - Model Resources", "Dedicated-process CPU, RSS and USS. CPU 100% is one logical core; RSS includes runtime/shared pages.", [
            ("CPU by model/process", "vh_resources", 'SELECT histogram(_timestamp) AS x, task, avg(cpu_percent) AS y FROM "vh_resources" GROUP BY x, task ORDER BY x', "cpu_percent"),
            ("RAM RSS by model/process (MiB)", "vh_resources", 'SELECT histogram(_timestamp) AS x, task, avg(rss_mib) AS y FROM "vh_resources" GROUP BY x, task ORDER BY x', "rss_mib"),
            ("Private RAM USS by model/process (MiB)", "vh_resources", 'SELECT histogram(_timestamp) AS x, task, avg(uss_mib) AS y FROM "vh_resources" GROUP BY x, task ORDER BY x', "uss_mib"),
            ("Latest resource samples", "vh_resources", 'SELECT * FROM "vh_resources" ORDER BY _timestamp DESC LIMIT 100'),
        ]),
        ("Virtual Human - Application Logs", "Application and library logs, exceptions and exporter health", [
            ("Application logs", "vh_application", 'SELECT * FROM "vh_application" ORDER BY _timestamp DESC LIMIT 100'),
        ]),
    ]
    rows = [row for _, _, group in specs for row in group]
    panels = [panel(i, *row[:3], metric=row[3] if len(row) > 3 else None) for i, row in enumerate(rows)]
    yield merge_research_dashboard({"version": 8, "title": "Virtual Human",
           "description": "CV, STT, LLM, TTS, pipeline events, application logs, and model/process CPU and RAM. CPU 100% is one logical core; RSS includes runtime and shared pages.",
           "tabs": [{"tabId": "main", "name": "Overview", "panels": panels}]}, panel)


def initialize_research_streams(client, api):
    """Establish query field types without creating measured research samples."""
    common = {"_timestamp": time.time_ns() // 1000, "event": "schema_initialized",
              "host": "setup", "experiment_id": "setup", "phase": "setup"}
    performance = {**common, "sample_id": "schema", "metric": "schema", "unit": "ms", "value": 0.,
                   "provider": "setup", "model": "setup", "phase": "setup", "status": "setup",
                   "timing_boundary": "setup", "measurement_source": "setup",
                   "image_width": 0, "image_height": 0, "people_count": 0}
    resources = {**common, "task": "setup", "resource_scope": "setup", "gpu_status": "setup",
                 "gpu_name": "setup", "gpu_id": "setup", "gpu_detail": "setup", "pid": 0,
                 **{key: 0. for key in ("host_cpu_percent", "core_cpu_percent", "cpu_pressure_percent",
                    "host_ram_percent", "gpu_busy_percent", "gpu_engine_percent", "vram_used_mib",
                    "vram_total_mib", "vram_used_percent", "gpu_resident_mib", "process_vram_mib")}}
    cv = {**common, "source_id": "setup", **{key: 0. for key in (
        "object_fps", "object_target_fps", "keypoint_fps", "keypoint_target_fps",
        "object_busy_skip_percent", "keypoint_busy_skip_percent")}}
    for stream, entry in (("vh_performance", performance), ("vh_resources", resources), ("vh_cv", cv)):
        ingest(client, f"{api}/{stream}/_json", [entry])


def validate_research_queries(client, api, dashboard):
    """Check every new saved query against this OpenObserve instance."""
    now = time.time_ns() // 1000
    for tab in dashboard["tabs"]:
        if tab.get("tabId") != "performance":
            continue
        for item in tab["panels"]:
            response = client.post(f"{api}/_search", json={"query": {
                "sql": item["queries"][0]["query"], "start_time": now - 86_400_000_000,
                "end_time": now + 1_000_000, "from": 0, "size": 1000}})
            if response.is_error or response.json().get("is_partial"):
                raise RuntimeError(f"Research query failed: {item['title']}: {response.text[:1000]}")
            print(f"Verified research panel: {item['title']}")


def panel_contract(dashboard):
    """The server adds defaults; compare saved chart behavior, not optional fields."""
    def axes(query):
        fields = query["fields"]
        return {axis: [{key: entry.get(key) for key in ("alias", "column", "label")}
                       for entry in fields.get(axis, [])] for axis in ("x", "y", "z", "breakdown")}

    return [(tab["tabId"], tab["name"], [
        {"id": item["id"], "title": item["title"], "type": item["type"],
         "layout": {key: item["layout"][key] for key in ("x", "y", "w", "h")},
         "queries": [(query["query"], axes(query)) for query in item["queries"]],
         "dynamic_columns": item.get("config", {}).get("table_dynamic_columns", False)}
        for item in tab["panels"]]) for tab in dashboard["tabs"]]


def ingest(client, url, entries):
    response = client.post(url, json=redact(entries))
    response.raise_for_status()
    data = response.json()
    if any(item.get("failed", 0) for item in data.get("status", []) if isinstance(item, dict)):
        raise RuntimeError("OpenObserve rejected log records: " + json.dumps(redact(data))[:1500])


def visible_table_columns(panel, rows):
    """Match the installed table renderer's mapped/dynamic column selection."""
    if panel["type"] != "table":
        return []
    mapped = [field["alias"] for query in panel["queries"]
              for axis in ("x", "y") for field in query["fields"].get(axis, [])]
    if panel.get("config", {}).get("table_dynamic_columns"):
        mapped.extend(key for row in rows for key in row if key not in mapped)
    return list(dict.fromkeys(mapped))


def dashboard_content(data):
    return data.get("v8", data)


def run(migrate=False, check=False, verify=False, consolidate=False, repair_tables=False, update_research=False):
    base = os.getenv("OPENOBSERVE_URL", "http://localhost:5080").rstrip("/")
    org = os.getenv("OPENOBSERVE_ORG", "default")
    api = f"{base}/api/{org}"
    with httpx.Client(timeout=30, **credentials()) as client:
        response = client.get(f"{api}/dashboards")
        response.raise_for_status()
        data = response.json()
        if check:
            print("OpenObserve authentication and dashboard access: OK")
            return
        managed = [dashboard_content(item) for item in data.get("dashboards", [])]
        managed = [item for item in managed if item.get("title") == "Virtual Human"]
        if update_research:
            if len(managed) != 1:
                raise RuntimeError("Expected exactly one Virtual Human dashboard")
            identifier = managed[0]["dashboardId"]
            saved_response = client.get(f"{api}/dashboards/{identifier}", params={"folder": "default"})
            saved_response.raise_for_status()
            saved = saved_response.json()
            updated = merge_research_dashboard(dashboard_content(saved), panel)
            initialize_research_streams(client, api)
            validate_research_queries(client, api, updated)
            params = {"folder": "default"}
            current_hash = saved.get("hash") or saved_response.headers.get("etag")
            if current_hash:
                params["hash"] = current_hash
            response = client.put(f"{api}/dashboards/{identifier}", params=params, json=updated)
            response.raise_for_status()
            response = client.get(f"{api}/dashboards/{identifier}", params={"folder": "default"})
            response.raise_for_status()
            actual = dashboard_content(response.json())
            if panel_contract(actual) != panel_contract(updated):
                raise RuntimeError("Saved research panels differ from the requested dashboard")
            validate_research_queries(client, api, actual)
            print("Updated Performance tab; existing Overview preserved")
            return
        if repair_tables or verify:
            if len(managed) != 1:
                raise RuntimeError("Expected exactly one Virtual Human dashboard")
            identifier = managed[0]["dashboardId"]
            saved_response = client.get(f"{api}/dashboards/{identifier}", params={"folder": "default"})
            saved_response.raise_for_status()
            saved = saved_response.json()
            stored = dashboard_content(saved)
        if repair_tables:
            count = 0
            for tab in stored.get("tabs", []):
                for item in tab.get("panels", []):
                    if item["type"] == "table":
                        item.setdefault("config", {})["table_dynamic_columns"] = True
                        count += 1
            params = {"folder": "default"}
            current_hash = saved.get("hash") or saved_response.headers.get("etag")
            if current_hash:
                params["hash"] = current_hash
            response = client.put(f"{api}/dashboards/{identifier}", params=params, json=stored)
            response.raise_for_status()
            check_response = client.get(f"{api}/dashboards/{identifier}", params={"folder": "default"})
            check_response.raise_for_status()
            repaired = dashboard_content(check_response.json())
            tables = [item for tab in repaired["tabs"] for item in tab["panels"] if item["type"] == "table"]
            if len(tables) != count or not all(item["config"].get("table_dynamic_columns") for item in tables):
                raise RuntimeError("Saved table-field verification failed")
            print(f"Repaired and verified {count} table tiles; existing queries, layout and charts preserved")
            return
        if verify:
            identifier = uuid.uuid4().hex
            stamp = time.time_ns() // 1000
            for kind in STREAMS:
                event = {"_timestamp": stamp, "event": "observability_verification", "verification_id": identifier}
                if kind == "resources":
                    event.update(resource_sample(psutil.Process(), "observability_verification"))
                emit(kind, event)
            shutdown()
            for stream in STREAMS.values():
                response = client.post(f"{api}/_search", json={"query": {
                    "sql": f"SELECT * FROM {stream} WHERE verification_id = '{identifier}'",
                    "start_time": stamp - 60_000_000, "end_time": time.time_ns() // 1000 + 1_000_000,
                    "from": 0, "size": 100}})
                response.raise_for_status()
                if not response.json().get("hits"):
                    raise RuntimeError(f"Exporter verification event not found in {stream}")
                print(f"Verified exporter delivery and search: {stream}")
            for tab in stored["tabs"]:
                for item in tab["panels"]:
                    response = client.post(f"{api}/_search", json={"query": {
                        "sql": item["queries"][0]["query"], "start_time": stamp - 86_400_000_000,
                        "end_time": time.time_ns() // 1000 + 1_000_000, "from": 0, "size": 100}})
                    if response.is_error:
                        raise RuntimeError(f"Dashboard SQL failed: {item['title']}: {response.text[:500]}")
                    rows = response.json().get("hits", [])
                    if (item["type"] == "table" and not visible_table_columns(item, rows)
                            and (rows or not item.get("config", {}).get("table_dynamic_columns"))):
                        raise RuntimeError(f"Table has no visible columns: {item['title']}")
                    print(f"Verified tile: {item['title']} ({len(rows)} rows)")
            print("All saved dashboard queries and visible table columns verified")
            return
        existing = {}
        for item in data.get("dashboards", []):
            content = item.get("v8", item.get("v5", item))
            if isinstance(content, dict):
                existing[content.get("title")] = content.get("dashboardId")
        now = time.time_ns() // 1000
        for kind, stream in STREAMS.items():
            ingest(client, f"{api}/{stream}/_json", [{"_timestamp": now, "event": "observability_configured",
                "service": "setup", "kind": kind}])
        # Ensure numeric metric fields exist before charts execute their queries.
        ingest(client, f"{api}/vh_resources/_json", [{"_timestamp": now, "event": "schema_initialized",
            "task": "setup", "cpu_percent": 0., "rss_mib": 0., "uss_mib": 0.}])
        ingest(client, f"{api}/vh_stages/_json", [{"_timestamp": now, "event": "schema_initialized",
            "stage": "setup", "duration_ms": 0.}])
        initialize_research_streams(client, api)
        for dashboard in dashboards():
            identifier = existing.get(dashboard["title"])
            if identifier:
                print(f"Exists: {dashboard['title']} ({identifier})")
                continue
            response = client.post(f"{api}/dashboards", json=dashboard)
            response.raise_for_status()
            result = response.json()
            content = result.get("v8", result)
            print(f"Created: {dashboard['title']} ({content.get('dashboardId', '')})")
            existing[dashboard["title"]] = content["dashboardId"]
        if consolidate:
            identifier = existing["Virtual Human"]
            response = client.get(f"{api}/dashboards/{identifier}")
            response.raise_for_status()
            stored = response.json().get("v8", response.json())
            expected = next(dashboards())["tabs"][0]["panels"]
            actual = stored.get("tabs", [{}])[0].get("panels", [])
            if {item["title"] for item in actual} != {item["title"] for item in expected}:
                raise RuntimeError("Combined dashboard panel verification failed; separate dashboards retained")
            legacy = {"Virtual Human - " + name for name in (
                "CV", "Conversation Pipeline", "LLM", "STT", "TTS", "Model Resources", "Application Logs")}
            for title in sorted(legacy):
                if existing.get(title):
                    response = client.delete(f"{api}/dashboards/{existing[title]}")
                    response.raise_for_status()
                    print(f"Consolidated and removed separate dashboard: {title}")
            print(f"Combined dashboard verified: {len(actual)} tiles ({identifier})")
        if migrate:
            root = Path(__file__).resolve().parents[1] / "logs"
            for path in sorted(root.rglob("*")) if root.exists() else []:
                if not path.is_file() or path.is_symlink():
                    continue
                kind = path.name.split("-", 1)[0]
                stream = STREAMS.get(kind, "vh_resources" if "resources" in str(path.parent) else "vh_application")
                batch = []
                for line in path.read_text(errors="replace").splitlines():
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        entry = {"message": line}
                    if not isinstance(entry, dict):
                        entry = {"message": entry}
                    stamp = entry.get("timestamp")
                    try:
                        entry["original_event_timestamp_us"] = int(datetime.fromisoformat(stamp).timestamp() * 1e6)
                    except (TypeError, ValueError):
                        entry["original_event_timestamp_us"] = now
                    # OpenObserve may reject events older than its ingestion window.
                    # Keep the original event time intact, and label this import explicitly.
                    entry["_timestamp"] = now
                    entry["historical_import"] = True
                    entry["legacy_source"] = path.name
                    batch.append(entry)
                    if len(batch) == 100:
                        ingest(client, f"{api}/{stream}/_json", batch)
                        batch = []
                if batch:
                    ingest(client, f"{api}/{stream}/_json", batch)
                path.unlink()  # User explicitly requested removal after export.
                print(f"Migrated and removed: {path.name}")
            if root.exists():
                for folder in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
                    folder.rmdir()
                root.rmdir()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrate-logs", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--consolidate", action="store_true")
    parser.add_argument("--repair-tables", action="store_true")
    parser.add_argument("--update-research", action="store_true", help="Update Performance panels, preserving Overview")
    args = parser.parse_args()
    run(args.migrate_logs, args.check, args.verify, args.consolidate, args.repair_tables, args.update_research)
