import threading
import time
from collections.abc import Callable
from typing import Any


class DownloadTracker:
    """Track download events and claim only downloads started by the current call."""

    def __init__(self, stopped: Callable[[], bool]) -> None:
        self._stopped = stopped
        self._lock = threading.Lock()
        self._pending_downloads: dict[str, dict[str, Any]] = {}

    def wait(
        self,
        timeout: float = 30.0,
        cancel_token: threading.Event | None = None,
        *,
        started_after: float = 0.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        guid: str | None = None
        while time.monotonic() < deadline:
            if self._stopped() or (cancel_token is not None and cancel_token.is_set()):
                return {"ok": False, "error": "Download wait was cancelled; the download may still complete"}
            with self._lock:
                if guid is None:
                    guid = next(
                        (key for key, value in self._pending_downloads.items() if value["started_at"] >= started_after),
                        None,
                    )
                entry = self._pending_downloads.get(guid) if guid is not None else None
                if entry is not None and entry["event"].is_set():
                    self._pending_downloads.pop(guid, None)
                    state = entry.get("state", "unknown")
                    if state == "completed":
                        return {
                            "ok": True,
                            "filename": entry.get("filename", ""),
                            "path": entry.get("file_path", ""),
                            "guid": guid,
                        }
                    return {"ok": False, "error": f"download ended with state: {state}"}
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return {"ok": False, "error": f"download timed out after {timeout}s; it may still complete"}

    def begin(self, params: dict[str, Any]) -> None:
        guid = params.get("guid", "")
        if not guid:
            return
        with self._lock:
            self._pending_downloads[guid] = {
                "state": "in_progress",
                "started_at": time.monotonic(),
                "filename": params.get("suggestedFilename", ""),
                "event": threading.Event(),
            }

    def progress(self, params: dict[str, Any]) -> None:
        guid = params.get("guid", "")
        state = params.get("state", "")
        with self._lock:
            entry = self._pending_downloads.get(guid)
            if entry is not None:
                entry["state"] = state
                if state == "completed":
                    entry["file_path"] = params.get("filePath", "")
                if state in ("completed", "canceled"):
                    entry["event"].set()
