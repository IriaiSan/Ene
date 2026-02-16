"""SQLite metrics store for the observatory.

Persists every LLM call record and provides efficient queries for
dashboards, cost analysis, health checks, and experiment tracking.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from loguru import logger


@dataclass
class LLMCallRecord:
    """A single LLM API call — the atomic unit of observatory data."""

    timestamp: str  # ISO 8601
    call_type: str  # "response" | "summary" | "diary" | "sleep" | "experiment"
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: int
    caller_id: str  # "discord:123456" or "system"
    session_key: str = ""
    tool_calls: list[str] = field(default_factory=list)
    finish_reason: str = "stop"
    error: str | None = None
    experiment_id: str | None = None
    variant_id: str | None = None

    def to_row(self) -> tuple:
        """Convert to a SQLite row tuple (excludes auto-increment id)."""
        return (
            self.timestamp,
            self.call_type,
            self.model,
            self.prompt_tokens,
            self.completion_tokens,
            self.total_tokens,
            self.cost_usd,
            self.latency_ms,
            self.caller_id,
            self.session_key,
            json.dumps(self.tool_calls),
            self.finish_reason,
            self.error,
            self.experiment_id,
            self.variant_id,
        )


# ── Schema ──────────────────────────────────────────────────

SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Every LLM call
CREATE TABLE IF NOT EXISTS llm_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    call_type       TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0.0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    caller_id       TEXT NOT NULL DEFAULT 'system',
    session_key     TEXT DEFAULT '',
    tool_calls      TEXT DEFAULT '[]',
    finish_reason   TEXT DEFAULT 'stop',
    error           TEXT,
    experiment_id   TEXT,
    variant_id      TEXT
);

-- Aggregated daily summaries (built lazily)
CREATE TABLE IF NOT EXISTS daily_summaries (
    date                TEXT PRIMARY KEY,
    total_calls         INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    total_cost_usd      REAL NOT NULL DEFAULT 0.0,
    avg_latency_ms      REAL NOT NULL DEFAULT 0.0,
    error_count         INTEGER NOT NULL DEFAULT 0,
    unique_callers      INTEGER NOT NULL DEFAULT 0,
    model_breakdown     TEXT DEFAULT '{}',
    call_type_breakdown TEXT DEFAULT '{}',
    caller_breakdown    TEXT DEFAULT '{}'
);

-- Experiments
CREATE TABLE IF NOT EXISTS experiments (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    variants        TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'active',
    target_calls    INTEGER DEFAULT 100,
    assignment_method TEXT DEFAULT 'random',
    created_at      TEXT NOT NULL,
    ended_at        TEXT,
    config          TEXT DEFAULT '{}'
);

-- Per-call experiment results
CREATE TABLE IF NOT EXISTS experiment_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   TEXT NOT NULL,
    variant_id      TEXT NOT NULL,
    call_id         INTEGER,
    quality_score   REAL,
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id),
    FOREIGN KEY (call_id) REFERENCES llm_calls(id)
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON llm_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_calls_model ON llm_calls(model);
CREATE INDEX IF NOT EXISTS idx_calls_caller ON llm_calls(caller_id);
CREATE INDEX IF NOT EXISTS idx_calls_type ON llm_calls(call_type);
CREATE INDEX IF NOT EXISTS idx_calls_experiment ON llm_calls(experiment_id);
CREATE INDEX IF NOT EXISTS idx_exp_results_exp ON experiment_results(experiment_id);
"""


class MetricsStore:
    """SQLite-backed metrics storage.

    Thread-safe — uses a connection per thread with WAL mode for
    concurrent read/write without blocking.
    """

    def __init__(self, db_path: Path | str):
        self._db_path = str(db_path)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        """Context manager for a cursor with auto-commit."""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        """Create tables and run migrations."""
        with self._cursor() as cur:
            cur.executescript(SCHEMA_SQL)
            # Set schema version
            cur.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
        logger.debug(f"Observatory DB initialized at {self._db_path}")

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    # ── Write ───────────────────────────────────────────────

    def record_call(self, record: LLMCallRecord) -> int:
        """Insert an LLM call record. Returns the row ID."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO llm_calls
                   (timestamp, call_type, model, prompt_tokens, completion_tokens,
                    total_tokens, cost_usd, latency_ms, caller_id, session_key,
                    tool_calls, finish_reason, error, experiment_id, variant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                record.to_row(),
            )
            return cur.lastrowid or 0

    def record_calls_batch(self, records: list[LLMCallRecord]) -> int:
        """Batch insert multiple records. Returns count inserted."""
        if not records:
            return 0
        with self._cursor() as cur:
            cur.executemany(
                """INSERT INTO llm_calls
                   (timestamp, call_type, model, prompt_tokens, completion_tokens,
                    total_tokens, cost_usd, latency_ms, caller_id, session_key,
                    tool_calls, finish_reason, error, experiment_id, variant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [r.to_row() for r in records],
            )
            return len(records)

    # ── Summary Queries ─────────────────────────────────────

    def get_today_summary(self) -> dict[str, Any]:
        """Get summary stats for today."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.get_day_summary(today)

    def get_day_summary(self, date: str) -> dict[str, Any]:
        """Get summary stats for a specific date (YYYY-MM-DD)."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT
                     COUNT(*) as total_calls,
                     COALESCE(SUM(total_tokens), 0) as total_tokens,
                     COALESCE(SUM(cost_usd), 0.0) as total_cost_usd,
                     COALESCE(AVG(latency_ms), 0.0) as avg_latency_ms,
                     COALESCE(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END), 0) as error_count,
                     COUNT(DISTINCT caller_id) as unique_callers
                   FROM llm_calls
                   WHERE timestamp LIKE ?""",
                (f"{date}%",),
            )
            row = cur.fetchone()

        if not row or row["total_calls"] == 0:
            return {
                "date": date,
                "total_calls": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "avg_latency_ms": 0.0,
                "error_count": 0,
                "unique_callers": 0,
                "model_breakdown": {},
                "call_type_breakdown": {},
                "caller_breakdown": {},
            }

        return {
            "date": date,
            "total_calls": row["total_calls"],
            "total_tokens": row["total_tokens"],
            "total_cost_usd": round(row["total_cost_usd"], 6),
            "avg_latency_ms": round(row["avg_latency_ms"], 1),
            "error_count": row["error_count"],
            "unique_callers": row["unique_callers"],
            "model_breakdown": self._get_breakdown(date, "model"),
            "call_type_breakdown": self._get_breakdown(date, "call_type"),
            "caller_breakdown": self._get_breakdown(date, "caller_id"),
        }

    def _get_breakdown(self, date: str, column: str) -> dict[str, dict[str, Any]]:
        """Get cost/call breakdown by a column for a date."""
        # column is one of our known columns — safe to interpolate
        allowed = {"model", "call_type", "caller_id"}
        if column not in allowed:
            return {}

        with self._cursor() as cur:
            cur.execute(
                f"""SELECT {column} as key,
                      COUNT(*) as calls,
                      COALESCE(SUM(cost_usd), 0.0) as cost,
                      COALESCE(SUM(total_tokens), 0) as tokens
                    FROM llm_calls
                    WHERE timestamp LIKE ?
                    GROUP BY {column}
                    ORDER BY cost DESC""",
                (f"{date}%",),
            )
            return {
                row["key"]: {
                    "calls": row["calls"],
                    "cost": round(row["cost"], 6),
                    "tokens": row["tokens"],
                }
                for row in cur.fetchall()
            }

    # ── Time-Series Queries ─────────────────────────────────

    def get_cost_by_day(self, days: int = 30) -> list[dict[str, Any]]:
        """Daily cost totals for the last N days."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._cursor() as cur:
            cur.execute(
                """SELECT
                     DATE(timestamp) as date,
                     COUNT(*) as calls,
                     COALESCE(SUM(cost_usd), 0.0) as cost,
                     COALESCE(SUM(total_tokens), 0) as tokens
                   FROM llm_calls
                   WHERE timestamp >= ?
                   GROUP BY DATE(timestamp)
                   ORDER BY date""",
                (since,),
            )
            return [
                {
                    "date": row["date"],
                    "calls": row["calls"],
                    "cost": round(row["cost"], 6),
                    "tokens": row["tokens"],
                }
                for row in cur.fetchall()
            ]

    def get_cost_by_model(self, days: int = 7) -> list[dict[str, Any]]:
        """Cost breakdown by model for the last N days."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._cursor() as cur:
            cur.execute(
                """SELECT model,
                     COUNT(*) as calls,
                     COALESCE(SUM(cost_usd), 0.0) as cost,
                     COALESCE(SUM(total_tokens), 0) as tokens,
                     COALESCE(AVG(latency_ms), 0.0) as avg_latency
                   FROM llm_calls
                   WHERE timestamp >= ?
                   GROUP BY model
                   ORDER BY cost DESC""",
                (since,),
            )
            return [
                {
                    "model": row["model"],
                    "calls": row["calls"],
                    "cost": round(row["cost"], 6),
                    "tokens": row["tokens"],
                    "avg_latency": round(row["avg_latency"], 1),
                }
                for row in cur.fetchall()
            ]

    def get_cost_by_caller(self, days: int = 7) -> list[dict[str, Any]]:
        """Cost breakdown by caller for the last N days."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._cursor() as cur:
            cur.execute(
                """SELECT caller_id,
                     COUNT(*) as calls,
                     COALESCE(SUM(cost_usd), 0.0) as cost,
                     COALESCE(SUM(total_tokens), 0) as tokens
                   FROM llm_calls
                   WHERE timestamp >= ?
                   GROUP BY caller_id
                   ORDER BY cost DESC""",
                (since,),
            )
            return [
                {
                    "caller_id": row["caller_id"],
                    "calls": row["calls"],
                    "cost": round(row["cost"], 6),
                    "tokens": row["tokens"],
                }
                for row in cur.fetchall()
            ]

    def get_cost_by_type(self, days: int = 7) -> list[dict[str, Any]]:
        """Cost breakdown by call type for the last N days."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._cursor() as cur:
            cur.execute(
                """SELECT call_type,
                     COUNT(*) as calls,
                     COALESCE(SUM(cost_usd), 0.0) as cost,
                     COALESCE(SUM(total_tokens), 0) as tokens,
                     COALESCE(AVG(latency_ms), 0.0) as avg_latency
                   FROM llm_calls
                   WHERE timestamp >= ?
                   GROUP BY call_type
                   ORDER BY cost DESC""",
                (since,),
            )
            return [
                {
                    "call_type": row["call_type"],
                    "calls": row["calls"],
                    "cost": round(row["cost"], 6),
                    "tokens": row["tokens"],
                    "avg_latency": round(row["avg_latency"], 1),
                }
                for row in cur.fetchall()
            ]

    def get_hourly_activity(self, days: int = 1) -> list[dict[str, Any]]:
        """Hourly call counts for the last N days."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        with self._cursor() as cur:
            cur.execute(
                """SELECT
                     STRFTIME('%Y-%m-%d %H:00', timestamp) as hour,
                     COUNT(*) as calls,
                     COALESCE(SUM(cost_usd), 0.0) as cost,
                     COALESCE(SUM(total_tokens), 0) as tokens,
                     COALESCE(AVG(latency_ms), 0.0) as avg_latency
                   FROM llm_calls
                   WHERE timestamp >= ?
                   GROUP BY hour
                   ORDER BY hour""",
                (since,),
            )
            return [
                {
                    "hour": row["hour"],
                    "calls": row["calls"],
                    "cost": round(row["cost"], 6),
                    "tokens": row["tokens"],
                    "avg_latency": round(row["avg_latency"], 1),
                }
                for row in cur.fetchall()
            ]

    # ── Health / Error Queries ──────────────────────────────

    def get_error_rate(self, hours: int = 24) -> dict[str, Any]:
        """Error rate and recent errors for the last N hours."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._cursor() as cur:
            cur.execute(
                """SELECT
                     COUNT(*) as total,
                     COALESCE(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END), 0) as errors
                   FROM llm_calls
                   WHERE timestamp >= ?""",
                (since,),
            )
            row = cur.fetchone()
            total = row["total"] if row else 0
            errors = row["errors"] if row else 0

            # Recent errors
            cur.execute(
                """SELECT timestamp, model, call_type, error, caller_id
                   FROM llm_calls
                   WHERE timestamp >= ? AND error IS NOT NULL
                   ORDER BY timestamp DESC
                   LIMIT 10""",
                (since,),
            )
            recent_errors = [dict(r) for r in cur.fetchall()]

        return {
            "hours": hours,
            "total_calls": total,
            "error_count": errors,
            "error_rate": round(errors / total, 4) if total > 0 else 0.0,
            "recent_errors": recent_errors,
        }

    def get_latency_percentiles(self, hours: int = 24) -> dict[str, Any]:
        """Latency percentiles for the last N hours."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._cursor() as cur:
            cur.execute(
                """SELECT latency_ms
                   FROM llm_calls
                   WHERE timestamp >= ? AND error IS NULL
                   ORDER BY latency_ms""",
                (since,),
            )
            latencies = [row["latency_ms"] for row in cur.fetchall()]

        if not latencies:
            return {"hours": hours, "count": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0}

        n = len(latencies)
        return {
            "hours": hours,
            "count": n,
            "p50": latencies[int(n * 0.50)],
            "p90": latencies[int(n * 0.90)] if n >= 10 else latencies[-1],
            "p95": latencies[int(n * 0.95)] if n >= 20 else latencies[-1],
            "p99": latencies[int(n * 0.99)] if n >= 100 else latencies[-1],
            "max": latencies[-1],
        }

    def get_recent_calls(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get the most recent LLM calls."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT id, timestamp, call_type, model, prompt_tokens,
                     completion_tokens, total_tokens, cost_usd, latency_ms,
                     caller_id, finish_reason, error, experiment_id, variant_id
                   FROM llm_calls
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_last_call_timestamp(self) -> str | None:
        """Get the timestamp of the most recent LLM call."""
        with self._cursor() as cur:
            cur.execute("SELECT MAX(timestamp) as ts FROM llm_calls")
            row = cur.fetchone()
            return row["ts"] if row else None

    def get_total_cost(self, days: int | None = None) -> float:
        """Get total cost, optionally limited to last N days."""
        if days:
            since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            with self._cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM llm_calls WHERE timestamp >= ?",
                    (since,),
                )
                row = cur.fetchone()
        else:
            with self._cursor() as cur:
                cur.execute("SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM llm_calls")
                row = cur.fetchone()
        return round(row["total"], 6) if row else 0.0

    def get_call_count(self, hours: int | None = None) -> int:
        """Get total call count, optionally limited to last N hours."""
        if hours:
            since = (datetime.now() - timedelta(hours=hours)).isoformat()
            with self._cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM llm_calls WHERE timestamp >= ?",
                    (since,),
                )
                row = cur.fetchone()
        else:
            with self._cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM llm_calls")
                row = cur.fetchone()
        return row["cnt"] if row else 0

    # ── Experiment Queries ──────────────────────────────────

    def create_experiment(self, experiment: dict[str, Any]) -> None:
        """Create a new experiment."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO experiments
                   (id, name, description, variants, status, target_calls,
                    assignment_method, created_at, config)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    experiment["id"],
                    experiment["name"],
                    experiment.get("description", ""),
                    json.dumps(experiment.get("variants", [])),
                    experiment.get("status", "active"),
                    experiment.get("target_calls", 100),
                    experiment.get("assignment_method", "random"),
                    experiment.get("created_at", datetime.now().isoformat()),
                    json.dumps(experiment.get("config", {})),
                ),
            )

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        """Get experiment by ID."""
        with self._cursor() as cur:
            cur.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,))
            row = cur.fetchone()
            if not row:
                return None
            result = dict(row)
            result["variants"] = json.loads(result["variants"])
            result["config"] = json.loads(result["config"])
            return result

    def get_active_experiments(self) -> list[dict[str, Any]]:
        """Get all active experiments."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM experiments WHERE status = 'active' ORDER BY created_at DESC"
            )
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["variants"] = json.loads(d["variants"])
                d["config"] = json.loads(d["config"])
                results.append(d)
            return results

    def get_all_experiments(self) -> list[dict[str, Any]]:
        """Get all experiments."""
        with self._cursor() as cur:
            cur.execute("SELECT * FROM experiments ORDER BY created_at DESC")
            results = []
            for row in cur.fetchall():
                d = dict(row)
                d["variants"] = json.loads(d["variants"])
                d["config"] = json.loads(d["config"])
                results.append(d)
            return results

    def update_experiment_status(self, experiment_id: str, status: str) -> None:
        """Update experiment status (active, paused, completed)."""
        with self._cursor() as cur:
            ended = datetime.now().isoformat() if status == "completed" else None
            cur.execute(
                "UPDATE experiments SET status = ?, ended_at = ? WHERE id = ?",
                (status, ended, experiment_id),
            )

    def record_experiment_result(
        self,
        experiment_id: str,
        variant_id: str,
        call_id: int | None = None,
        quality_score: float | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Record an experiment result."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO experiment_results
                   (experiment_id, variant_id, call_id, quality_score, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    experiment_id,
                    variant_id,
                    call_id,
                    quality_score,
                    json.dumps(metadata or {}),
                    datetime.now().isoformat(),
                ),
            )

    def get_experiment_results(self, experiment_id: str) -> dict[str, Any]:
        """Get aggregated results for an experiment."""
        with self._cursor() as cur:
            # Per-variant stats from llm_calls
            cur.execute(
                """SELECT variant_id,
                     COUNT(*) as calls,
                     COALESCE(AVG(cost_usd), 0.0) as avg_cost,
                     COALESCE(AVG(latency_ms), 0.0) as avg_latency,
                     COALESCE(SUM(cost_usd), 0.0) as total_cost,
                     COALESCE(AVG(total_tokens), 0) as avg_tokens,
                     COALESCE(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END), 0) as errors
                   FROM llm_calls
                   WHERE experiment_id = ?
                   GROUP BY variant_id""",
                (experiment_id,),
            )
            variant_stats = {
                row["variant_id"]: {
                    "calls": row["calls"],
                    "avg_cost": round(row["avg_cost"], 6),
                    "avg_latency": round(row["avg_latency"], 1),
                    "total_cost": round(row["total_cost"], 6),
                    "avg_tokens": round(row["avg_tokens"]),
                    "errors": row["errors"],
                }
                for row in cur.fetchall()
            }

            # Quality scores from experiment_results
            cur.execute(
                """SELECT variant_id,
                     COUNT(*) as scored,
                     COALESCE(AVG(quality_score), 0.0) as avg_quality,
                     COALESCE(MIN(quality_score), 0.0) as min_quality,
                     COALESCE(MAX(quality_score), 0.0) as max_quality
                   FROM experiment_results
                   WHERE experiment_id = ? AND quality_score IS NOT NULL
                   GROUP BY variant_id""",
                (experiment_id,),
            )
            for row in cur.fetchall():
                vid = row["variant_id"]
                if vid in variant_stats:
                    variant_stats[vid].update({
                        "scored": row["scored"],
                        "avg_quality": round(row["avg_quality"], 3),
                        "min_quality": round(row["min_quality"], 3),
                        "max_quality": round(row["max_quality"], 3),
                    })

        return {
            "experiment_id": experiment_id,
            "variants": variant_stats,
            "total_calls": sum(v["calls"] for v in variant_stats.values()),
        }

    # ── Utilities ───────────────────────────────────────────

    def get_average_daily_cost(self, days: int = 7) -> float:
        """Get average daily cost over the last N days (for spike detection)."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._cursor() as cur:
            cur.execute(
                """SELECT COALESCE(AVG(daily_cost), 0.0) as avg
                   FROM (
                     SELECT DATE(timestamp) as d, SUM(cost_usd) as daily_cost
                     FROM llm_calls
                     WHERE timestamp >= ?
                     GROUP BY DATE(timestamp)
                   )""",
                (since,),
            )
            row = cur.fetchone()
            return round(row["avg"], 6) if row else 0.0

    def vacuum(self) -> None:
        """Reclaim space. Run periodically (e.g., weekly)."""
        conn = self._get_conn()
        conn.execute("VACUUM")
