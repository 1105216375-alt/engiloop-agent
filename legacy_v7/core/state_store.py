"""Durable SQLite state store for sessions, reviewed memory and task state."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 3


def default_state_db_path() -> Path:
    configured = os.environ.get("ENGINEERING_AGENT_STATE_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    raise ValueError("Pass an explicit state database path or ENGINEERING_AGENT_STATE_DB")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class SQLiteStateStore:
    """Thread-safe repository with forward-only schema migrations."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or default_state_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), timeout=30, check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        current = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()["version"]
        if current < 1:
            self._apply_v1()
            self._conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, time.time()),
            )
            current = 1
        if current < 2:
            self._apply_v2()
            self._conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (2, time.time()),
            )
            current = 2
        if current < 3:
            self._apply_v3()
            self._conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (3, time.time()),
            )
        self._conn.commit()

    def _apply_v1(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_type TEXT NOT NULL,
                session_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL,
                version INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (session_type, session_id)
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry
                ON sessions(expires_at);

            CREATE TABLE IF NOT EXISTS memory_candidates (
                candidate_id TEXT PRIMARY KEY,
                pattern_hash TEXT NOT NULL UNIQUE,
                memory_type TEXT NOT NULL,
                task_type TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'pending',
                confidence REAL NOT NULL,
                payload_json TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                reviewed_at REAL,
                reviewed_by TEXT,
                review_note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
                ON memory_candidates(review_status, task_type, last_seen_at DESC);

            CREATE TABLE IF NOT EXISTS memory_versions (
                memory_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                candidate_id TEXT NOT NULL,
                pattern_hash TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                task_type TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                confidence REAL NOT NULL,
                payload_json TEXT NOT NULL,
                approved_at REAL NOT NULL,
                approved_by TEXT NOT NULL,
                supersedes_memory_id TEXT,
                PRIMARY KEY (memory_id, version),
                FOREIGN KEY(candidate_id) REFERENCES memory_candidates(candidate_id)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_versions_lookup
                ON memory_versions(status, task_type, scope_type, scope_key);

            CREATE TABLE IF NOT EXISTS run_checkpoints (
                run_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                stage TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (run_id, step_index)
            );

            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_id TEXT PRIMARY KEY,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payload_json TEXT NOT NULL,
                requested_at REAL NOT NULL,
                decided_at REAL,
                decided_by TEXT,
                decision_note TEXT
            );

            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'queued',
                payload_json TEXT NOT NULL,
                result_json TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                lease_owner TEXT,
                lease_expires_at REAL,
                next_attempt_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_subject
                ON audit_events(subject_type, subject_id, created_at DESC);
            """
        )

    def _apply_v2(self) -> None:
        """Reject cost questions that v1 classified as major-repair patterns."""
        rows = self._conn.execute(
            """
            SELECT candidate_id, payload_json FROM memory_candidates
            WHERE review_status='pending' AND task_type='major_repair_decision'
            """
        ).fetchall()
        for row in rows:
            payload = _json_loads(row["payload_json"], {})
            goal = str(payload.get("goal_template") or "")
            if any(word in goal for word in ("费用", "成本", "预算", "多少钱", "造价")):
                self._conn.execute(
                    """
                    UPDATE memory_candidates SET review_status='rejected',
                        reviewed_at=?, reviewed_by='schema_migration_v2',
                        review_note='造价意图应优先于大修名词，等待按repair_cost重新提取'
                    WHERE candidate_id=?
                    """,
                    (time.time(), row["candidate_id"]),
                )

    def _apply_v3(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_tokens (
                token_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked_at REAL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_auth_tokens_lookup
                ON auth_tokens(token_hash, expires_at, revoked_at);
            """
        )

    # Sessions ---------------------------------------------------------
    def save_session(
        self,
        session_type: str,
        session_id: str,
        payload: Dict[str, Any],
        created_at: float,
        updated_at: float,
        expires_at: Optional[float],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions(
                    session_type, session_id, payload_json, created_at,
                    updated_at, expires_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(session_type, session_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at,
                    version=sessions.version + 1
                """,
                (
                    session_type, session_id, _json_dumps(payload), created_at,
                    updated_at, expires_at,
                ),
            )

    def load_session(self, session_type: str, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_type=? AND session_id=?",
                (session_type, session_id),
            ).fetchone()
        if not row:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            self.delete_session(session_type, session_id)
            return None
        return {
            "payload": _json_loads(row["payload_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
            "version": row["version"],
        }

    def delete_session(self, session_type: str, session_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM sessions WHERE session_type=? AND session_id=?",
                (session_type, session_id),
            )

    def prune_expired_sessions(self) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE expires_at IS NOT NULL AND expires_at < ?",
                (time.time(),),
            )
            return cursor.rowcount

    # Euler-style reviewed memory -------------------------------------
    def upsert_memory_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        candidate_id = candidate.get("candidate_id") or f"memcand_{uuid.uuid4().hex[:16]}"
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT * FROM memory_candidates WHERE pattern_hash=?",
                (candidate["pattern_hash"],),
            ).fetchone()
            if existing:
                payload_json = existing["payload_json"]
                if existing["review_status"] == "pending":
                    payload_json = _json_dumps(candidate["payload"])
                self._conn.execute(
                    """
                    UPDATE memory_candidates SET
                        payload_json=?, confidence=MAX(confidence, ?),
                        occurrence_count=occurrence_count + 1, last_seen_at=?
                    WHERE candidate_id=?
                    """,
                    (payload_json, candidate["confidence"], now, existing["candidate_id"]),
                )
                candidate_id = existing["candidate_id"]
            else:
                self._conn.execute(
                    """
                    INSERT INTO memory_candidates(
                        candidate_id, pattern_hash, memory_type, task_type,
                        scope_type, scope_key, review_status, confidence,
                        payload_json, occurrence_count, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, 1, ?, ?)
                    """,
                    (
                        candidate_id, candidate["pattern_hash"], candidate["memory_type"],
                        candidate["task_type"], candidate["scope_type"],
                        candidate.get("scope_key", ""), candidate["confidence"],
                        _json_dumps(candidate["payload"]), now, now,
                    ),
                )
        return self.get_memory_candidate(candidate_id) or {}

    def get_memory_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        return self._candidate_row(row) if row else None

    def list_memory_candidates(
        self, review_status: str = "pending", limit: int = 50,
    ) -> List[Dict[str, Any]]:
        limit = min(max(int(limit), 1), 200)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM memory_candidates
                WHERE review_status=? ORDER BY last_seen_at DESC LIMIT ?
                """,
                (review_status, limit),
            ).fetchall()
        return [self._candidate_row(row) for row in rows]

    def review_memory_candidate(
        self,
        candidate_id: str,
        decision: str,
        reviewer: str,
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM memory_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                """
                UPDATE memory_candidates SET review_status=?, reviewed_at=?,
                    reviewed_by=?, review_note=? WHERE candidate_id=?
                """,
                (decision, now, reviewer, note, candidate_id),
            )
            if decision == "approved":
                existing = self._conn.execute(
                    "SELECT memory_id, version FROM memory_versions "
                    "WHERE pattern_hash=? ORDER BY version DESC LIMIT 1",
                    (row["pattern_hash"],),
                ).fetchone()
                memory_id = existing["memory_id"] if existing and existing["memory_id"] else \
                    f"memory_{uuid.uuid4().hex[:16]}"
                version = int(existing["version"] or 0) + 1 if existing else 1
                self._conn.execute(
                    "UPDATE memory_versions SET status='superseded' "
                    "WHERE pattern_hash=? AND status='active'",
                    (row["pattern_hash"],),
                )
                self._conn.execute(
                    """
                    INSERT INTO memory_versions(
                        memory_id, version, candidate_id, pattern_hash, memory_type,
                        task_type, scope_type, scope_key, status, confidence,
                        payload_json, approved_at, approved_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        memory_id, version, candidate_id, row["pattern_hash"],
                        row["memory_type"], row["task_type"], row["scope_type"],
                        row["scope_key"], row["confidence"], row["payload_json"],
                        now, reviewer,
                    ),
                )
            else:
                # Retain the version and audit history, but stop all previously
                # published versions of this pattern from participating in reuse.
                self._conn.execute(
                    "UPDATE memory_versions SET status='revoked' "
                    "WHERE pattern_hash=? AND status='active'",
                    (row["pattern_hash"],),
                )
            self._append_audit_locked(
                "memory_review", reviewer, "memory_candidate", candidate_id,
                {"decision": decision, "note": note}, now,
            )
        return self.get_memory_candidate(candidate_id)

    def list_approved_memories(
        self,
        task_type: str = "",
        scope_type: str = "pattern",
        scope_key: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        clauses = [
            "status='active'",
            # Also protects existing databases with rejected candidates whose
            # active version predates the revocation fix, without deleting data.
            "EXISTS (SELECT 1 FROM memory_candidates c "
            "WHERE c.candidate_id=memory_versions.candidate_id AND c.review_status='approved')",
            "version=(SELECT MAX(v.version) FROM memory_versions v "
            "WHERE v.memory_id=memory_versions.memory_id)",
        ]
        args: List[Any] = []
        if task_type:
            clauses.append("task_type=?")
            args.append(task_type)
        if scope_type:
            clauses.append("scope_type=?")
            args.append(scope_type)
        if scope_key:
            clauses.append("scope_key=?")
            args.append(scope_key)
        args.append(min(max(int(limit), 1), 200))
        query = (
            "SELECT * FROM memory_versions WHERE " + " AND ".join(clauses)
            + " ORDER BY approved_at DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [self._memory_row(row) for row in rows]

    def _candidate_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["payload"] = _json_loads(result.pop("payload_json"), {})
        return result

    def _memory_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["payload"] = _json_loads(result.pop("payload_json"), {})
        return result

    # Checkpoints and audit -------------------------------------------
    def save_checkpoint(
        self, run_id: str, step_index: int, stage: str,
        state_hash: str, payload: Dict[str, Any],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO run_checkpoints(
                    run_id, step_index, stage, state_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, step_index, stage, state_hash, _json_dumps(payload), time.time()),
            )

    def latest_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM run_checkpoints WHERE run_id=?
                ORDER BY step_index DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = _json_loads(result.pop("payload_json"), {})
        return result

    def list_checkpoints(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM run_checkpoints
                ORDER BY created_at DESC LIMIT ?
                """,
                (min(max(int(limit), 1), 200),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json_loads(item.pop("payload_json"), {})
            result.append(item)
        return result

    # Durable jobs ----------------------------------------------------
    def enqueue_job(
        self,
        job_type: str,
        idempotency_key: str,
        payload: Dict[str, Any],
        *,
        max_attempts: int = 3,
        waiting_approval: bool = False,
    ) -> Dict[str, Any]:
        now = time.time()
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        status = "waiting_approval" if waiting_approval else "queued"
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing:
                return self._job_row(existing)
            self._conn.execute(
                """
                INSERT INTO jobs(
                    job_id, job_type, idempotency_key, status, payload_json,
                    attempt_count, max_attempts, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    job_id, job_type, idempotency_key, status, _json_dumps(payload),
                    max(1, min(int(max_attempts), 10)), now, now, now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return self._job_row(row)

    def claim_job(self, worker_id: str, lease_seconds: int = 120) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE jobs SET status='retry', lease_owner=NULL,
                        lease_expires_at=NULL, next_attempt_at=?, updated_at=?,
                        last_error=COALESCE(last_error, 'worker lease expired')
                    WHERE status='running' AND lease_expires_at < ?
                    """,
                    (now, now, now),
                )
                row = self._conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status IN ('queued', 'retry')
                      AND COALESCE(next_attempt_at, 0) <= ?
                      AND attempt_count < max_attempts
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (now,),
                ).fetchone()
                if not row:
                    self._conn.commit()
                    return None
                self._conn.execute(
                    """
                    UPDATE jobs SET status='running', attempt_count=attempt_count + 1,
                        lease_owner=?, lease_expires_at=?, updated_at=?
                    WHERE job_id=?
                    """,
                    (worker_id, now + lease_seconds, now, row["job_id"]),
                )
                claimed = self._conn.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)
                ).fetchone()
                self._conn.commit()
                return self._job_row(claimed)
            except Exception:
                self._conn.rollback()
                raise

    def complete_job(self, job_id: str, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE jobs SET status='completed', result_json=?, lease_owner=NULL,
                    lease_expires_at=NULL, updated_at=?, last_error=NULL
                WHERE job_id=?
                """,
                (_json_dumps(result), now, job_id),
            )
        return self.get_job(job_id)

    def fail_job(self, job_id: str, error: str, retry_delay: float) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT attempt_count, max_attempts FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if not row:
                return None
            exhausted = int(row["attempt_count"]) >= int(row["max_attempts"])
            status = "failed" if exhausted else "retry"
            self._conn.execute(
                """
                UPDATE jobs SET status=?, next_attempt_at=?, lease_owner=NULL,
                    lease_expires_at=NULL, updated_at=?, last_error=?
                WHERE job_id=?
                """,
                (status, now + max(0.0, retry_delay), now, error[:2000], job_id),
            )
        return self.get_job(job_id)

    def release_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE jobs SET status='queued', next_attempt_at=?, updated_at=?
                WHERE job_id=? AND status='waiting_approval'
                """,
                (now, now, job_id),
            )
        return self.get_job(job_id)

    def cancel_job(self, job_id: str, reason: str = "rejected") -> Optional[Dict[str, Any]]:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE jobs SET status='cancelled', updated_at=?, last_error=?
                WHERE job_id=? AND status IN ('waiting_approval', 'queued', 'retry')
                """,
                (time.time(), reason[:2000], job_id),
            )
        return self.get_job(job_id)

    def retry_failed_job(self, job_id: str, actor: str = "system") -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT status FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if not row:
                return None
            if row["status"] != "failed":
                return self.get_job(job_id)
            self._conn.execute(
                """
                UPDATE jobs SET status='queued', attempt_count=0,
                    lease_owner=NULL, lease_expires_at=NULL,
                    next_attempt_at=?, updated_at=?, last_error=NULL
                WHERE job_id=? AND status='failed'
                """,
                (now, now, job_id),
            )
            self._append_audit_locked(
                "job_manual_retry", actor, "job", job_id, {}, now,
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return self._job_row(row) if row else None

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (min(max(int(limit), 1), 200),),
            ).fetchall()
        return [self._job_row(row) for row in rows]

    def _job_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["payload"] = _json_loads(result.pop("payload_json"), {})
        result["result"] = _json_loads(result.pop("result_json"), None)
        return result

    # Approval queue --------------------------------------------------
    def create_approval_request(
        self,
        subject_type: str,
        subject_id: str,
        risk_level: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock, self._conn:
            existing = self._conn.execute(
                """
                SELECT * FROM approval_requests
                WHERE subject_type=? AND subject_id=? AND status='pending'
                ORDER BY requested_at DESC LIMIT 1
                """,
                (subject_type, subject_id),
            ).fetchone()
            if existing:
                return self._approval_row(existing)
            approval_id = f"approval_{uuid.uuid4().hex[:16]}"
            self._conn.execute(
                """
                INSERT INTO approval_requests(
                    approval_id, subject_type, subject_id, risk_level,
                    status, payload_json, requested_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id, subject_type, subject_id, risk_level,
                    _json_dumps(payload), now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)
            ).fetchone()
        return self._approval_row(row)

    def decide_approval(
        self, approval_id: str, decision: str, reviewer: str, note: str = "",
    ) -> Optional[Dict[str, Any]]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        now = time.time()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if not row:
                return None
            if row["status"] != "pending":
                return self._approval_row(row)
            self._conn.execute(
                """
                UPDATE approval_requests SET status=?, decided_at=?,
                    decided_by=?, decision_note=? WHERE approval_id=?
                """,
                (decision, now, reviewer, note[:1000], approval_id),
            )
            self._append_audit_locked(
                "approval_decision", reviewer, row["subject_type"], row["subject_id"],
                {"approval_id": approval_id, "decision": decision, "note": note}, now,
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)
            ).fetchone()
        return self._approval_row(row) if row else None

    def latest_approval(self, subject_type: str, subject_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest decision snapshot for one governed subject."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM approval_requests
                WHERE subject_type=? AND subject_id=?
                ORDER BY requested_at DESC LIMIT 1
                """,
                (subject_type, subject_id),
            ).fetchone()
        return self._approval_row(row) if row else None

    def list_approvals(self, status: str = "pending", limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM approval_requests WHERE status=?
                ORDER BY requested_at DESC LIMIT ?
                """,
                (status, min(max(int(limit), 1), 200)),
            ).fetchall()
        return [self._approval_row(row) for row in rows]

    def _approval_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["payload"] = _json_loads(result.pop("payload_json"), {})
        return result

    # Users and bearer tokens ----------------------------------------
    def user_count(self) -> int:
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) AS count FROM users WHERE active=1"
            ).fetchone()["count"])

    def create_user(self, username: str, password_hash: str, role: str) -> Dict[str, Any]:
        now = time.time()
        user_id = f"user_{uuid.uuid4().hex[:16]}"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO users(
                    user_id, username, password_hash, role, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (user_id, username, password_hash, role, now, now),
            )
        return self.get_user_by_username(username) or {}

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username=? AND active=1", (username,)
            ).fetchone()
        return dict(row) if row else None

    def save_auth_token(
        self, user_id: str, token_hash: str, expires_at: float,
    ) -> str:
        token_id = f"token_{uuid.uuid4().hex[:16]}"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO auth_tokens(
                    token_id, user_id, token_hash, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (token_id, user_id, token_hash, time.time(), expires_at),
            )
        return token_id

    def resolve_auth_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT t.token_id, t.user_id, t.expires_at,
                       u.username, u.role, u.active
                FROM auth_tokens t JOIN users u ON u.user_id=t.user_id
                WHERE t.token_hash=? AND t.revoked_at IS NULL
                  AND t.expires_at>? AND u.active=1
                """,
                (token_hash, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def revoke_auth_token(self, token_hash: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE auth_tokens SET revoked_at=?
                WHERE token_hash=? AND revoked_at IS NULL
                """,
                (time.time(), token_hash),
            )
        return bool(cursor.rowcount)

    def append_audit(
        self, event_type: str, actor: str, subject_type: str,
        subject_id: str, payload: Dict[str, Any],
    ) -> str:
        with self._lock, self._conn:
            return self._append_audit_locked(
                event_type, actor, subject_type, subject_id, payload, time.time(),
            )

    def _append_audit_locked(
        self, event_type: str, actor: str, subject_type: str,
        subject_id: str, payload: Dict[str, Any], created_at: float,
    ) -> str:
        event_id = f"audit_{uuid.uuid4().hex[:16]}"
        self._conn.execute(
            """
            INSERT INTO audit_events(
                event_id, event_type, actor, subject_type, subject_id,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, event_type, actor or "system", subject_type,
                subject_id, _json_dumps(payload), created_at,
            ),
        )
        return event_id

    def status(self) -> Dict[str, Any]:
        with self._lock:
            counts = {}
            for table in (
                "sessions", "memory_candidates", "memory_versions",
                "run_checkpoints", "approval_requests", "jobs", "audit_events",
                "users", "auth_tokens",
            ):
                counts[table] = self._conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table}"
                ).fetchone()["count"]
            version = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()["version"]
        return {
            "ready": True,
            "backend": "sqlite",
            "path": str(self.path),
            "schema_version": version,
            "counts": counts,
        }


_STORE: Optional[SQLiteStateStore] = None
_STORE_LOCK = threading.Lock()


def get_state_store() -> SQLiteStateStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = SQLiteStateStore()
    return _STORE
