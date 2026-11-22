import json
import sqlite3
from contextlib import closing, contextmanager

from app.errors import ReviewError
from app.manifest import canonical


class RunStore:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS wafer_runs (
                    run_id TEXT PRIMARY KEY,
                    manifest TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    pinned TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS wafer_adjudications (
                    run_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 1
                )
            """)

    def put(self, manifest, snapshot, pinned):
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO wafer_runs VALUES (?, ?, ?, ?) ON CONFLICT(run_id) DO NOTHING",
                (manifest["run_id"], canonical(manifest), canonical(snapshot), canonical(pinned)),
            )
            created = cursor.rowcount == 1
            row = connection.execute("SELECT manifest FROM wafer_runs WHERE run_id = ?",
                                     (manifest["run_id"],)).fetchone()
        return json.loads(row[0]), created

    def get(self, run_id):
        with self.connect() as connection:
            row = connection.execute("SELECT manifest, snapshot, pinned FROM wafer_runs WHERE run_id = ?",
                                     (run_id,)).fetchone()
        if row is None:
            raise ReviewError("RUN_NOT_FOUND", "图批未找到", 404)
        return tuple(json.loads(part) for part in row)

    def list(self):
        with self.connect() as connection:
            rows = connection.execute("SELECT manifest FROM wafer_runs ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_adjudication(self, run_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT decision, reason, decided_at, attempt_count FROM wafer_adjudications WHERE run_id = ?",
                (run_id,)).fetchone()
        if row is None:
            return None
        return {"decision": row[0], "reason": row[1], "decided_at": row[2], "attempt_count": row[3]}

    def save_adjudication(self, run_id, decision, reason, decided_at):
        """幂等写入：同档同裁决重试不新增记录，只累加 attempt_count。

        返回 (裁决记录, created)。已有不同裁决时返回 (既有记录, None)，
        由服务层报冲突；本方法不改写既有裁决，保证裁决可审计。
        先取 BEGIN IMMEDIATE 写锁再判定，把"检查-写入"串行化，并发首裁
        也只会落一行；created 取自插入分支本身，不依赖 rowcount 语义。
        """
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT decision, reason, decided_at, attempt_count FROM wafer_adjudications"
                " WHERE run_id = ?", (run_id,)).fetchone()
            if existing is not None:
                if existing[0] != decision:
                    return {"decision": existing[0], "reason": existing[1], "decided_at": existing[2],
                            "attempt_count": existing[3]}, None
                connection.execute(
                    "UPDATE wafer_adjudications SET attempt_count = attempt_count + 1 WHERE run_id = ?",
                    (run_id,))
                row = connection.execute(
                    "SELECT decision, reason, decided_at, attempt_count FROM wafer_adjudications"
                    " WHERE run_id = ?", (run_id,)).fetchone()
                return {"decision": row[0], "reason": row[1], "decided_at": row[2],
                        "attempt_count": row[3]}, False
            connection.execute(
                "INSERT INTO wafer_adjudications VALUES (?, ?, ?, ?, 1)",
                (run_id, decision, reason, decided_at))
        return {"decision": decision, "reason": reason, "decided_at": decided_at,
                "attempt_count": 1}, True

    def list_adjudications(self):
        with self.connect() as connection:
            rows = connection.execute("SELECT run_id, decision, reason, decided_at, attempt_count"
                                      " FROM wafer_adjudications").fetchall()
        return {row[0]: {"decision": row[1], "reason": row[2], "decided_at": row[3],
                         "attempt_count": row[4]} for row in rows}
