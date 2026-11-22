from fastapi.testclient import TestClient

from app.main import create_app


def test_mixed_pipeline_reaches_all_seven_modules_and_evidence_export(client, run_base):
    routes = {"/intake": "files", "/coordinates": "rows", "/mask/W-07": "cells", "/retest/W-07": "issues",
              "/clusters/W-07/C01": "points", "/yield": "wafers", "": "sources", "/export": "manifest"}
    for route, key in routes.items():
        response = client.get(run_base + route)
        assert response.status_code == 200, route
        assert response.json()[key], route
    assert client.post(run_base + "/rerun").json()["verified"] is True


def test_workbench_pages_serve_chinese_controls_and_local_assets(client):
    pages = {"/":"有效芯粒", "/intake":"图档采信", "/coordinates":"坐标对表", "/retest":"复测裁决",
             "/clusters":"缺陷簇", "/yield":"良率勾稽", "/runs":"图批复盘"}
    for path, title in pages.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert f"<h1>{title}</h1>" in response.text
        assert all(control in response.text for control in ('id="sample"','id="start"','id="rerun"','id="export"'))
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/components.css").status_code == 200


def test_baseline_has_no_clusters_and_matches_reference(client):
    started = client.post("/api/runs", json={"sample":"baseline"}).json()
    manifest = started["manifest"]
    url = f'/api/runs/{manifest["run_id"]}'
    assert manifest["summary"]["valid"] == 47 and manifest["summary"]["yield_pct"] == "100.00"
    assert client.get(url + "/yield").json()["status"] == "MATCH"
    assert client.get(url + "/clusters/W-03").json() == []


def test_fresh_startup_seeds_once_across_restart(tmp_path):
    database = tmp_path / "seed.sqlite3"
    with TestClient(create_app(database)) as test:
        initial = test.get("/api/runs").json()
        assert len(initial) == 1 and initial[0]["sample"] == "mixed"
        assert test.get("/healthz").json() == {"status":"ok"}
    with TestClient(create_app(database)) as test:
        assert test.get("/api/runs").json() == initial


def test_unknown_sample_run_and_page_have_explicit_errors(client):
    attempts = [client.post("/api/runs", json={"sample":"unknown"}), client.get("/api/runs/unknown")]
    for response, code in zip(attempts, ["SAMPLE_NOT_FOUND", "RUN_NOT_FOUND"]):
        assert response.status_code == 404 and response.json()["code"] == code
    assert client.get("/unknown").status_code == 404


def test_legacy_batch_flows_through_all_modules_with_quarantine_and_verdict(client):
    started = client.post("/api/runs", json={"sample": "legacy"})
    assert started.status_code == 201
    run_id = started.json()["manifest"]["run_id"]
    base = f"/api/runs/{run_id}"
    intake = client.get(base + "/intake").json()
    assert intake["intake"] == {"state": "QUARANTINED", "unknown_segments": 1}
    assert {seg["status"] for seg in intake["files"][0]["segments_detail"]} == {"recognized", "unknown"}
    # 可识别段继续走完对表、掩模、裁决、簇、勾稽
    for route, key in [("/coordinates", "rows"), ("/mask/W-03", "cells"), ("/retest/W-03", "cells"),
                       ("/clusters/W-03", "points"), ("/yield", "wafers")]:
        response = client.get(base + route)
        assert response.status_code == 200, route
    audit = client.get(base + "/yield").json()
    assert audit["rows"]["balanced"] is True
    # 资料员在采信页裁决，重试不重复建档
    verdict = client.post(base + "/adjudication", json={"decision": "ACCEPT_PARTIAL", "reason": "夜班"})
    assert verdict.status_code == 201
    retry = client.post(base + "/adjudication", json={"decision": "ACCEPT_PARTIAL", "reason": "夜班"})
    assert retry.status_code == 200 and retry.json()["adjudication"]["attempt_count"] == 2
    assert client.post(base + "/rerun").json()["verified"] is True
    exported = client.get(base + "/export")
    assert exported.status_code == 200 and exported.json()["manifest"]["run_id"] == run_id
    assert len(client.get("/api/runs").json()) == 1
