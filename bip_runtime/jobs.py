"""Small durable worker backed by SQLite leases and idempotent jobs."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional


JobHandler = Callable[[Dict[str, Any]], Dict[str, Any]]


class DurableJobWorker:
    def __init__(
        self,
        state_store: Any,
        handlers: Dict[str, JobHandler],
        *,
        poll_interval: float = 0.5,
        lease_seconds: int = 300,
    ) -> None:
        self.state_store = state_store
        self.handlers = dict(handlers)
        self.poll_interval = max(0.1, poll_interval)
        self.lease_seconds = max(30, lease_seconds)
        self.worker_id = f"worker_{uuid.uuid4().hex[:12]}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"bip-job-{self.worker_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def notify(self) -> None:
        self._wake.set()

    def run_once(self) -> bool:
        job = self.state_store.claim_job(self.worker_id, self.lease_seconds)
        if not job:
            return False
        attempt = max(int(job.get("attempt_count") or 1), 1)
        step_base = attempt * 1000
        self._checkpoint(job, step_base + 1, "job_started", {
            "status": "running",
            "attempt": attempt,
            "worker_id": self.worker_id,
        })
        handler = self.handlers.get(job["job_type"])
        if not handler:
            failed = self.state_store.fail_job(
                job["job_id"], f"unknown job type: {job['job_type']}", retry_delay=0,
            )
            self._checkpoint(job, step_base + 998, "job_failed", {
                "status": (failed or {}).get("status", "failed"),
                "error": f"unknown job type: {job['job_type']}",
            })
            return True
        try:
            handler_payload = dict(job.get("payload") or {})
            handler_payload["_job_context"] = {
                "job_id": job["job_id"],
                "attempt": attempt,
                "worker_id": self.worker_id,
                "step_base": step_base,
            }
            result = handler(handler_payload)
            completed = self.state_store.complete_job(job["job_id"], result or {})
            self._checkpoint(job, step_base + 999, "job_completed", {
                "status": (completed or {}).get("status", "completed"),
                "result_keys": sorted((result or {}).keys()),
            })
        except Exception as exc:
            delay = min(2 ** (attempt - 1), 60)
            failed = self.state_store.fail_job(
                job["job_id"], f"{type(exc).__name__}: {exc}", retry_delay=delay,
            )
            self._checkpoint(job, step_base + 998, "job_retry", {
                "status": (failed or {}).get("status", "retry"),
                "retry_delay_seconds": delay,
                "error": f"{type(exc).__name__}: {exc}"[:1000],
            })
        return True

    def _checkpoint(
        self, job: Dict[str, Any], step_index: int, stage: str,
        payload: Dict[str, Any],
    ) -> None:
        try:
            canonical = json.dumps(
                {"job_id": job["job_id"], "stage": stage, "payload": payload},
                ensure_ascii=False, sort_keys=True, default=str,
            )
            self.state_store.save_checkpoint(
                job["job_id"], step_index, stage,
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(), payload,
            )
        except Exception:
            # Checkpoint telemetry must not make the underlying job fail.
            return

    def _run(self) -> None:
        while not self._stop.is_set():
            worked = self.run_once()
            if worked:
                continue
            self._wake.wait(self.poll_interval)
            self._wake.clear()
