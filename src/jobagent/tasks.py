"""Single local worker with durable tasks, atomic claims and explicit recovery."""
import json
import os
import threading
from pathlib import Path

from jobagent.database import Database, json_text, utc_now
from jobagent.llm import create_provider
from jobagent.platsbanken import Platsbanken
from jobagent.processing import analyze_pending, analyze_stored_job
from jobagent.search import collect_sources
from jobagent.settings import Search, LLMSettings


class WorkerLock:
    """OS lock prevents two web processes from running against one task queue."""
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise RuntimeError("Another JobAgent web worker is already using this database") from None

    def release(self):
        if self.stream:
            self.stream.close()
            self.stream = None


class TaskQueue:
    def __init__(self, database: Database, settings, provider_factory=create_provider):
        self.database, self.settings = database, settings
        self.provider_factory = provider_factory
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.lock = WorkerLock(database.path.with_suffix(database.path.suffix + ".worker.lock"))

    def recover(self):
        with self.database.connect() as conn:
            conn.execute("UPDATE background_tasks SET status='interrupted', updated_at=?, error_message=? WHERE status='running'",
                         (utc_now(), "Avbruten vid omstart. Starta om uppgiften för att fortsätta."))
            conn.execute("UPDATE search_runs SET status='interrupted', finished_at=? WHERE status='running'", (utc_now(),))

    def enqueue(self, kind: str, payload: dict | None = None) -> int:
        if kind not in {"collect", "analyze_pending", "analyze_one"}:
            raise ValueError("Unknown task kind")
        payload = dict(payload or {})
        if kind in {"analyze_pending", "analyze_one"}:
            payload.setdefault("llm", self.settings.llm.model_dump())
            LLMSettings.model_validate(payload["llm"])
        if kind == "collect":
            Search.model_validate(payload)
        if kind == "analyze_one" and self.database.get_job(int(payload["job_id"])) is None:
            raise ValueError("No stored job with that ID")
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            serialized = json_text(payload)
            existing = conn.execute("SELECT id FROM background_tasks WHERE kind=? AND payload_json=? AND status IN ('queued','running')",
                                    (kind, serialized)).fetchone()
            if existing:
                return existing["id"]
            task_id = conn.execute("INSERT INTO background_tasks(kind,payload_json,status,created_at,updated_at) VALUES(?,?,'queued',?,?)",
                                  (kind, serialized, utc_now(), utc_now())).lastrowid
        self.wake.set()
        return int(task_id)

    def list(self, limit=50):
        with self.database.connect(read_only=True) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM background_tasks ORDER BY id DESC LIMIT ?", (limit,))]

    def get(self, task_id):
        with self.database.connect(read_only=True) as conn:
            row = conn.execute("SELECT * FROM background_tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def retry(self, task_id):
        row = self.get(task_id)
        if not row or row["status"] not in {"interrupted", "error", "partial"}:
            raise ValueError("Only interrupted, partial or failed tasks can be restarted")
        return self.enqueue(row["kind"], json.loads(row["payload_json"]))

    def progress(self, task_id, value):
        if self.stop_event.is_set():
            raise InterruptedError("Worker is stopping")
        with self.database.connect() as conn:
            conn.execute("UPDATE background_tasks SET progress_json=?,updated_at=? WHERE id=?", (json_text(value), utc_now(), task_id))

    def run_once(self):
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM background_tasks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return False
            row = dict(row)
            conn.execute("UPDATE background_tasks SET status='running',updated_at=? WHERE id=?", (utc_now(), row["id"]))
        result, error, status = None, None, "success"
        try:
            payload = json.loads(row["payload_json"])
            llm_settings = LLMSettings.model_validate(payload["llm"]) if "llm" in payload else self.settings.llm
            progress = lambda value: self.progress(row["id"], value)
            if row["kind"] == "collect":
                with Platsbanken(self.settings.source) as source:
                    results = collect_sources(self.database, [source], Search.model_validate(payload), progress)
                result = {"sources": results}
                if any(r["status"] != "complete" for r in results):
                    status = "error" if all(r["status"] == "failed" for r in results) else "partial"
            elif row["kind"] == "analyze_pending":
                result = analyze_pending(self.database, self.provider_factory(llm_settings), self.settings.filter, progress)
                if result["failed"]:
                    status = "partial" if result["succeeded"] else "error"
            else:
                job = self.database.get_job(payload["job_id"])
                result = analyze_stored_job(self.database, job, self.provider_factory(llm_settings), self.settings.filter)
                status = "error" if result["status"] == "error" else "success"
                error = result.get("error")
            if status in {"error", "partial"} and not error:
                error = "Alla delar kunde inte slutföras. Se resultat och analysfel; uppgiften kan startas om."
        except InterruptedError:
            status, error = "interrupted", "Avbruten vid avstängning. Starta om för att fortsätta."
        except Exception as exc:
            status, error = "error", f"Uppgiften misslyckades ({type(exc).__name__}). Kontrollera inställningarna och starta om."
        with self.database.connect() as conn:
            conn.execute("UPDATE background_tasks SET status=?, result_json=?,error_message=?,updated_at=? WHERE id=?",
                         (status, json_text(result), error, utc_now(), row["id"]))
        return True

    def start(self):
        self.lock.acquire()
        try:
            self.recover()
        except BaseException:
            self.lock.release()
            raise
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="jobagent-worker", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            if not self.run_once():
                self.wake.wait(0.5)
                self.wake.clear()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            # Finish the bounded in-flight task before releasing exclusive ownership.
            self.thread.join()
        self.lock.release()
