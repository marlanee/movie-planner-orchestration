"""이벤트를 즉시 저장하고 실행이 끝나면 규칙으로 판정한다."""

from collections.abc import Callable
from threading import Lock, Thread

from ..config import GMS_MODEL, REPLAN_LIMIT
from ..events.bus import EventBus
from ..store import anomaly_repo, logs_repo
from .diagnoser import diagnose
from .evaluator import evaluate_run, split_runs


class LoggingAgent:
    def __init__(
        self, append: Callable[[dict], None] = logs_repo.append_event, *,
        diagnose_run: Callable[[dict, list[dict]], dict] = diagnose,
    ):
        self._append = append
        self._diagnose = diagnose_run
        self._lock = Lock()
        self._unsubscribe: Callable[[], None] | None = None
        self.saved_count = 0
        self.failed_count = 0

    def start(self, bus: EventBus) -> None:
        with self._lock:
            if self._unsubscribe is None:
                self._unsubscribe = bus.subscribe(self.handle)

    def stop(self) -> None:
        with self._lock:
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None

    def handle(self, event: dict) -> None:
        try:
            self._append(event)
        except Exception:
            self._record_failure()
            return
        with self._lock:
            self.saved_count += 1

        if (event["type"], event["stage"]) in {("stage_end", "respond"), ("error", "agent")}:
            try:
                self._report_run(event)
            except Exception:
                self._record_failure()

    def _report_run(self, terminal: dict) -> None:
        session_id = terminal["session_id"]
        events = logs_repo.get_session_events(session_id)
        if not events or any(events[-1][key] != terminal[key] for key in ("seq", "ts")):
            return
        runs = split_runs(events)
        run = runs[-1]
        report = evaluate_run(run, REPLAN_LIMIT, run_index=len(runs))
        report_id = anomaly_repo.append_report(session_id, report, terminal)
        if report_id is not None and report["verdict"] == "anomaly":
            Thread(target=self._diagnose_and_save, args=(report_id, report, run), daemon=True).start()

    def _diagnose_and_save(self, report_id: int, report: dict, run: list[dict]) -> None:
        try:
            diagnosis = self._diagnose(report, run)
        except Exception:
            diagnosis = {"status": "failed", "text": None}
        if diagnosis["status"] == "failed":
            self._record_failure()
        try:
            anomaly_repo.append_diagnosis(report_id, diagnosis, GMS_MODEL)
        except Exception:
            self._record_failure()

    def _record_failure(self) -> None:
        with self._lock:
            self.failed_count += 1
