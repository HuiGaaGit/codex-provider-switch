from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
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
