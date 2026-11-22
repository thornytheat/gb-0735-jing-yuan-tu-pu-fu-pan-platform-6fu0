from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture
def legacy_run(client):
    response = client.post("/api/runs", json={"sample": "legacy"})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["manifest"]["run_id"], body["manifest"]


def test_legacy_run_manifest_carries_quarantine_state(client, legacy_run):
    run_id, manifest = legacy_run
    assert manifest["intake"] == {"state": "QUARANTINED", "unknown_segments": 1}
    assert manifest["adjudication"] is None
    listed = client.get("/api/runs").json()
    entry = next(item for item in listed if item["run_id"] == run_id)
    assert entry["intake"]["state"] == "QUARANTINED" and entry["adjudication"] is None


def test_intake_endpoint_lists_segments_issues_and_pending_verdict(client, legacy_run):
    run_id, _ = legacy_run
    data = client.get(f"/api/runs/{run_id}/intake").json()
    assert data["intake"]["unknown_segments"] == 1 and data["adjudication"] is None
    detail = data["files"][0]["segments_detail"]
    assert [seg["status"] for seg in detail] == ["recognized", "recognized", "unknown", "recognized"]
    codes = {row["code"] for row in data["issues"]}
    assert {"UNKNOWN_HEADER", "UNKNOWN_SEGMENT", "COLUMN_COUNT", "BLANK_ROW", "REPEATED_HEADER"} <= codes


def test_accept_partial_keeps_recognized_segments_and_quarantines_unknown(client, legacy_run):
    run_id, _ = legacy_run
    reply = client.post(f"/api/runs/{run_id}/adjudication",
                        json={"decision": "ACCEPT_PARTIAL", "reason": "接收可识别段，旧机台段隔离待退"})
    assert reply.status_code == 201, reply.text
    verdict = reply.json()["adjudication"]
    assert verdict["decision"] == "ACCEPT_PARTIAL" and verdict["attempt_count"] == 1
    stored = client.get(f"/api/runs/{run_id}").json()
    assert stored["adjudication"]["decision"] == "ACCEPT_PARTIAL"
    # 可识别段形成的采信结果仍可继续对表
    decisions = client.get(f"/api/runs/{run_id}/retest/W-03").json()
    measured = [cell for cell in decisions["cells"] if cell["attempt"] is not None]
    assert len(measured) == 7
    assert all(cell["result"] in {"PASS", "FAIL"} for cell in measured)
    audit = client.get(f"/api/runs/{run_id}/yield").json()
    assert audit["rows"]["balanced"] is True
    assert sum(audit["reasons"].get(code, 0) for code in ("UNKNOWN_HEADER", "UNKNOWN_SEGMENT")) == 3


def test_retry_same_decision_is_idempotent_and_accumulates_once(client, legacy_run):
    run_id, _ = legacy_run
    payload = {"decision": "RETURN_BATCH", "reason": "栏名待客户确认"}
    first = client.post(f"/api/runs/{run_id}/adjudication", json=payload)
    second = client.post(f"/api/runs/{run_id}/adjudication", json=payload)
    third = client.post(f"/api/runs/{run_id}/adjudication", json={**payload, "reason": "栏名待客户确认"})
    assert first.status_code == 201 and second.status_code == 200 and third.status_code == 200
    assert {r.json()["adjudication"]["attempt_count"] for r in (first, second, third)} == {1, 2, 3}
    assert all(r.json()["reused"] is False for r in [first]) and second.json()["reused"] is True
    # 裁决表只有一行，运行表也只有一行，重试不重复累加记录
    store = client.app.state.runs.store
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM wafer_adjudications").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM wafer_runs").fetchone()[0] == 1


def test_same_input_rerun_keeps_original_run_id_and_adjudication(client, legacy_run):
    run_id, _ = legacy_run
    client.post(f"/api/runs/{run_id}/adjudication", json={"decision": "RETURN_BATCH", "reason": "整批退回"})
    repeat = client.post("/api/runs", json={"sample": "legacy"})
    assert repeat.status_code == 200 and repeat.json()["manifest"]["run_id"] == run_id
    assert repeat.json()["manifest"]["adjudication"]["decision"] == "RETURN_BATCH"
    assert len(client.get("/api/runs").json()) == 1
    assert client.post(f"/api/runs/{run_id}/rerun").json()["verified"] is True
    assert client.get(f"/api/runs/{run_id}").json()["adjudication"]["decision"] == "RETURN_BATCH"


def test_changing_a_recorded_decision_is_locked(client, legacy_run):
    run_id, _ = legacy_run
    client.post(f"/api/runs/{run_id}/adjudication", json={"decision": "RETURN_BATCH", "reason": "先退"})
    conflict = client.post(f"/api/runs/{run_id}/adjudication",
                           json={"decision": "ACCEPT_PARTIAL", "reason": "改收"})
    assert conflict.status_code == 409 and conflict.json()["code"] == "ADJUDICATION_LOCKED"
    stored = client.get(f"/api/runs/{run_id}").json()
    assert stored["adjudication"]["decision"] == "RETURN_BATCH"
    assert stored["adjudication"]["attempt_count"] == 1


def test_accept_partial_on_clean_batch_is_rejected(client, run_base):
    reply = client.post(f"{run_base}/adjudication",
                        json={"decision": "ACCEPT_PARTIAL", "reason": "无未知段"})
    assert reply.status_code == 422 and reply.json()["code"] == "NO_UNKNOWN_SEGMENT"
    assert client.get(run_base).json()["adjudication"] is None


def test_return_batch_works_on_clean_batch_and_bad_decision_rejected(client, run_base):
    reply = client.post(f"{run_base}/adjudication", json={"decision": "RETURN_BATCH", "reason": ""})
    assert reply.status_code == 201 and reply.json()["adjudication"]["reason"] == ""
    bad = client.post(f"{run_base}/adjudication", json={"decision": "HOLD", "reason": ""})
    assert bad.status_code == 422 and bad.json()["code"] == "BAD_DECISION"


def test_adjudicating_unknown_run_returns_404(client):
    reply = client.post("/api/runs/nonexistent/adjudication",
                        json={"decision": "RETURN_BATCH", "reason": ""})
    assert reply.status_code == 404 and reply.json()["code"] == "RUN_NOT_FOUND"


def test_adjudication_persists_across_app_restart(tmp_path, client, legacy_run):
    run_id, _ = legacy_run
    client.post(f"/api/runs/{run_id}/adjudication", json={"decision": "ACCEPT_PARTIAL", "reason": "夜班裁决"})
    database = client.app.state.runs.store.path
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(database, seed=False)) as restarted:
        detail = restarted.get(f"/api/runs/{run_id}").json()
        assert detail["adjudication"]["decision"] == "ACCEPT_PARTIAL"
        assert detail["adjudication"]["reason"] == "夜班裁决"


def test_storage_interruption_during_adjudication_leaves_previous_state_untouched(client, legacy_run):
    run_id, _ = legacy_run
    store = client.app.state.runs.store
    with store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_adj AFTER INSERT ON wafer_adjudications "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    failed = client.post(f"/api/runs/{run_id}/adjudication",
                         json={"decision": "ACCEPT_PARTIAL", "reason": "中断"})
    assert failed.status_code == 503 and failed.json()["code"] == "STORAGE_ERROR"
    with store.connect() as connection:
        connection.execute("DROP TRIGGER fail_adj")
        assert connection.execute("SELECT COUNT(*) FROM wafer_adjudications").fetchone()[0] == 0
    # 旧运行完整结果仍可查回，且随后可正常裁决
    assert client.get(f"/api/runs/{run_id}/intake").status_code == 200
    ok = client.post(f"/api/runs/{run_id}/adjudication",
                     json={"decision": "RETURN_BATCH", "reason": "恢复后退回"})
    assert ok.status_code == 201 and ok.json()["adjudication"]["attempt_count"] == 1


def test_concurrent_first_adjudication_never_duplicates(client, legacy_run):
    run_id, _ = legacy_run
    service = client.app.state.runs
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(
            lambda _: service.adjudicate(run_id, "RETURN_BATCH", "并发"), range(4)))
    assert len(outcomes) == 4
    assert len({item["adjudication"]["decided_at"] for item in outcomes}) == 1
    store = service.store
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM wafer_adjudications").fetchone()[0] == 1
    assert store.get_adjudication(run_id)["attempt_count"] == 4
