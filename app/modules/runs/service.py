from datetime import datetime, timezone
from sqlite3 import IntegrityError
from time import perf_counter

from app.config import RULE_VERSION
from app.errors import ReviewError
from app.manifest import canonical, digest, fingerprint, pin
from app.modules.intake.catalog import load_sample, restore_package
from app.modules.runs.pipeline import calculate

DECISIONS = ("RETURN_BATCH", "ACCEPT_PARTIAL")


def intake_state(snapshot):
    unknown = sum(file.get("unknown_segments", 0) for file in snapshot["files"])
    return {"state": "QUARANTINED" if unknown else "CLEAN", "unknown_segments": unknown}


def with_adjudication(manifest, adjudication, snapshot):
    """把段级采信状态与资料员裁决合并进对外清单，不改动归档本体。"""
    return {**manifest, "intake": intake_state(snapshot), "adjudication": adjudication}


class RunService:
    def __init__(self, store, loader=load_sample):
        self.store = store
        self.loader = loader

    def start(self, sample):
        started = perf_counter()
        package = self.loader(sample)
        key, sources, contract = fingerprint(package)
        snapshot = calculate(package)
        result_hash = digest(canonical(snapshot).encode())
        manifest = {"run_id": key, "sample": sample, "label": package.spec.label, "lot": package.spec.lot,
                    "wafers": [wafer.wafer for wafer in package.spec.wafers], "rule_version": RULE_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "duration_ms": round((perf_counter() - started) * 1000), "sources": sources,
                    "contract_sha256": contract, "result_sha256": result_hash,
                    "summary": snapshot["audit"]["summary"], "status": snapshot["audit"]["status"]}
        recorded, created = self.store.put(manifest, snapshot, pin(package))
        if recorded["result_sha256"] != result_hash:
            raise ReviewError("REPLAY_DIVERGED", "同版结果发生变化", 409)
        adjudication = self.store.get_adjudication(key)
        return {"manifest": with_adjudication(recorded, adjudication, snapshot), "reused": not created}

    def list_runs(self):
        records = self.store.list()
        adjudications = self.store.list_adjudications()
        enriched = []
        for manifest in records:
            _, snapshot, _ = self.store.get(manifest["run_id"])
            enriched.append(with_adjudication(manifest, adjudications.get(manifest["run_id"]), snapshot))
        return enriched

    def run_detail(self, run_id):
        manifest, snapshot, _ = self.store.get(run_id)
        return with_adjudication(manifest, self.store.get_adjudication(run_id), snapshot)

    def adjudicate(self, run_id, decision, reason):
        if decision not in DECISIONS:
            raise ReviewError("BAD_DECISION", "裁决动作无效", 422)
        _, snapshot, _ = self.store.get(run_id)
        state = intake_state(snapshot)
        if decision == "ACCEPT_PARTIAL" and state["state"] != "QUARANTINED":
            raise ReviewError("NO_UNKNOWN_SEGMENT", "本批无未知段，无需分段接收", 422)
        decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            record, created = self.store.save_adjudication(run_id, decision, reason, decided_at)
        except IntegrityError as exc:
            # 并发下另一事务抢先落了不同裁决
            existing = self.store.get_adjudication(run_id)
            if existing and existing["decision"] != decision:
                raise ReviewError("ADJUDICATION_LOCKED",
                                  f"本批已裁决为 {existing['decision']}，不能改为 {decision}", 409) from exc
            raise
        if created is None:
            raise ReviewError("ADJUDICATION_LOCKED",
                              f"本批已裁决为 {record['decision']}，不能改为 {decision}", 409)
        return {"adjudication": record, "reused": not created, "intake": state}

    def rerun(self, run_id):
        manifest, snapshot, pinned = self.store.get(run_id)
        if manifest["rule_version"] != RULE_VERSION:
            raise ReviewError("RULE_VERSION_CHANGED", "旧规则版本无法重算", 409)
        package = restore_package(pinned)
        key, _, _ = fingerprint(package)
        if key != run_id or digest(canonical(snapshot).encode()) != manifest["result_sha256"]:
            raise ReviewError("SNAPSHOT_DAMAGED", "归档摘要不符", 409)
        actual = digest(canonical(calculate(package)).encode())
        if actual != manifest["result_sha256"]:
            raise ReviewError("REPLAY_DIVERGED", "复算摘要不符", 409)
        return {"manifest": manifest, "reused": True, "verified": True}
