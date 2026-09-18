from __future__ import annotations

import json
import time
import bisect
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


MAX_HISTORY_SECONDS = 7 * 24 * 3600


@dataclass(slots=True)
class HealthRecord:
    timestamp: float
    profile_id: str
    state: str
    latency_ms: int | None = None


@dataclass(slots=True)
class ProviderSummary:
    profile_id: str
    total_checks: int = 0
    healthy_checks: int = 0
    error_checks: int = 0
    avg_latency_ms: float | None = None

    @property
    def availability(self) -> float | None:
        if self.total_checks == 0:
            return None
        return self.healthy_checks / self.total_checks * 100.0

    @property
    def error_rate(self) -> float | None:
        if self.total_checks == 0:
            return None
        return self.error_checks / self.total_checks * 100.0


class MonitorHistory:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "monitor-history.jsonl"

    def append(self, records: list[HealthRecord]) -> None:
        if not records:
            return
        lines = [
            json.dumps(asdict(r), ensure_ascii=False, separators=(",", ":")) + "\n"
            for r in records
        ]
        with self.path.open("a", encoding="utf-8") as fh:
            fh.writelines(lines)
        self._prune()

    def load(self, max_age_seconds: float = MAX_HISTORY_SECONDS) -> list[HealthRecord]:
        if not self.path.exists():
            return []
        cutoff = time.time() - max_age_seconds
        records: list[HealthRecord] = []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = float(obj.get("timestamp", 0))
                    if ts < cutoff:
                        continue
                    records.append(
                        HealthRecord(
                            timestamp=ts,
                            profile_id=str(obj.get("profile_id", "")),
                            state=str(obj.get("state", "")),
                            latency_ms=obj.get("latency_ms"),
                        )
                    )
        except OSError:
            return []
        return records

    @staticmethod
    def load_codex_request_records(codex_home: Path, max_age_seconds: float) -> list[HealthRecord]:
        """Read request outcomes emitted by Codex itself.

        This is deliberately local log ingestion: it never sends a probe to a
        provider. A completed task is a successful request, an aborted turn is
        a warning, and an upstream response item with an error status is an
        offline result.
        """
        cutoff = time.time() - max_age_seconds
        switch_times: list[float] = []
        switch_profiles: list[str] = []
        switch_path = codex_home / "codex-provider-switch" / "switch-history.jsonl"
        try:
            for line in switch_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(line)
                    stamp = str(item.get("timestamp", "")).replace("Z", "+00:00")
                    ts = datetime.fromisoformat(stamp).timestamp()
                    profile = str(item.get("profile_id", "")).strip()
                    if profile:
                        switch_times.append(ts)
                        switch_profiles.append(profile)
                except (json.JSONDecodeError, TypeError, ValueError, OverflowError):
                    continue
        except OSError:
            pass
        paired = sorted(zip(switch_times, switch_profiles), key=lambda item: item[0])
        switch_times = [item[0] for item in paired]
        switch_profiles = [item[1] for item in paired]
        records: list[HealthRecord] = []
        for root_name in ("sessions", "archived_sessions"):
            root = codex_home / root_name
            if not root.exists():
                continue
            for path in root.rglob("*.jsonl"):
                if not path.is_file():
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
                provider = "unknown"
                request_started: float | None = None
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as stream:
                        for line in stream:
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            timestamp = event.get("timestamp")
                            try:
                                stamp = str(timestamp).replace("Z", "+00:00")
                                parsed = datetime.fromisoformat(stamp)
                                if parsed.tzinfo is None:
                                    ts = parsed.timestamp()
                                else:
                                    ts = parsed.timestamp()
                            except (TypeError, ValueError, OverflowError):
                                ts = path.stat().st_mtime
                            if ts < cutoff:
                                continue
                            if event.get("type") == "session_meta":
                                value = event.get("payload", {}).get("model_provider")
                                if isinstance(value, str) and value:
                                    provider = value
                                continue
                            state = ""
                            payload = event.get("payload", {})
                            if event.get("type") == "event_msg":
                                kind = payload.get("type")
                                if kind == "task_started":
                                    request_started = ts
                                elif kind == "task_complete":
                                    state = "healthy"
                                elif kind == "turn_aborted":
                                    state = "warning"
                            elif event.get("type") == "response_item":
                                status = str(payload.get("status", "")).casefold()
                                if status in {"failed", "error", "incomplete"}:
                                    state = "offline"
                            if state:
                                if switch_times:
                                    switch_index = bisect.bisect_right(switch_times, ts) - 1
                                    if switch_index >= 0:
                                        provider = switch_profiles[switch_index]
                                latency_ms = None
                                # Codex exposes the time to the first model
                                # token on task_complete. This is the
                                # provider response latency; duration_ms also
                                # includes local tools and user interaction.
                                if payload.get("type") == "task_complete":
                                    try:
                                        candidate = int(payload.get("time_to_first_token_ms", 0) or 0)
                                        if candidate > 0:
                                            latency_ms = candidate
                                    except (TypeError, ValueError):
                                        pass
                                if latency_ms is None and request_started is not None and ts >= request_started:
                                    latency_ms = max(0, round((ts - request_started) * 1000))
                                records.append(HealthRecord(ts, provider, state, latency_ms))
                                request_started = None
                except (OSError, ValueError):
                    continue
        records.sort(key=lambda item: item.timestamp)
        return records

    def summarize(
        self, records: list[HealthRecord], profile_ids: list[str]
    ) -> dict[str, ProviderSummary]:
        result = {pid: ProviderSummary(profile_id=pid) for pid in profile_ids}
        latency_totals: dict[str, int] = {}
        latency_counts: dict[str, int] = {}
        for rec in records:
            entry = result.get(rec.profile_id)
            if entry is None:
                entry = ProviderSummary(profile_id=rec.profile_id)
                result[rec.profile_id] = entry
            entry.total_checks += 1
            if rec.state in {"healthy", "warning"}:
                entry.healthy_checks += 1
            elif rec.state in {"offline", "auth_error", "unconfigured"}:
                entry.error_checks += 1
            if rec.latency_ms is not None:
                latency_totals[rec.profile_id] = latency_totals.get(rec.profile_id, 0) + rec.latency_ms
                latency_counts[rec.profile_id] = latency_counts.get(rec.profile_id, 0) + 1
                entry.avg_latency_ms = (
                    latency_totals[rec.profile_id] / latency_counts[rec.profile_id]
                )
        return result

    def timeline_buckets(
        self,
        records: list[HealthRecord],
        profile_id: str,
        window_seconds: float,
        bucket_count: int = 80,
    ) -> list[str]:
        """Return a list of state strings for each time bucket ('up','warn','down','none')."""
        now = time.time()
        start = now - window_seconds
        bucket_width = window_seconds / bucket_count
        buckets: list[str] = ["none"] * bucket_count
        for rec in records:
            if rec.profile_id != profile_id:
                continue
            idx = int((rec.timestamp - start) / bucket_width)
            if 0 <= idx < bucket_count:
                if rec.state in {"healthy"}:
                    state = "up"
                elif rec.state in {"warning"}:
                    state = "warn"
                elif rec.state in {"offline", "auth_error", "unconfigured"}:
                    state = "down"
                else:
                    state = "none"
                if buckets[idx] == "none" or (buckets[idx] == "up" and state in {"warn", "down"}):
                    buckets[idx] = state
        return buckets

    def _prune(self) -> None:
        cutoff = time.time() - MAX_HISTORY_SECONDS
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        kept: list[str] = []
        for line in lines:
            try:
                obj = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if float(obj.get("timestamp", 0)) >= cutoff:
                kept.append(line)
        if len(kept) < len(lines):
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=str(self.data_dir), suffix=".tmp")
            try:
                with open(fd, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(kept) + "\n")
                tmp_path = Path(tmp)
                tmp_path.replace(self.path)
            except OSError:
                pass
