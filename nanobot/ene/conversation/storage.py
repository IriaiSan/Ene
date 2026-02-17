"""Disk persistence for conversation threads.

Handles saving/loading active thread state and archiving dead threads.
Thread state is saved as JSON (active.json) for fast reload on restart.
Dead threads are appended to daily JSONL archives for later analysis.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from .models import Thread, PendingMessage


class ThreadStorage:
    """Manages thread persistence on disk.

    Layout:
        {thread_dir}/
            active.json          — all non-DEAD threads (snapshot)
            pending.json         — pending single messages
            archive/
                2026-02-18.jsonl — dead threads archived by date
    """

    def __init__(self, thread_dir: Path) -> None:
        self._dir = thread_dir
        self._active_path = thread_dir / "active.json"
        self._pending_path = thread_dir / "pending.json"
        self._archive_dir = thread_dir / "archive"

    def ensure_dirs(self) -> None:
        """Create storage directories if they don't exist."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    # ── Active threads ───────────────────────────────────────────────

    def save_active(
        self,
        threads: dict[str, Thread],
        pending: list[PendingMessage] | None = None,
    ) -> None:
        """Save all non-dead threads to active.json.

        Atomic write: writes to .tmp then renames to avoid corruption.
        """
        try:
            data = {
                "saved_at": datetime.now().isoformat(),
                "thread_count": len(threads),
                "threads": {tid: t.to_dict() for tid, t in threads.items()},
            }
            tmp = self._active_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self._active_path)

            # Save pending messages separately
            if pending is not None:
                pending_data = {
                    "saved_at": datetime.now().isoformat(),
                    "count": len(pending),
                    "messages": [
                        {
                            "message": p.message.to_dict(),
                            "channel_key": p.channel_key,
                            "created_at": p.created_at,
                        }
                        for p in pending
                    ],
                }
                ptmp = self._pending_path.with_suffix(".tmp")
                ptmp.write_text(json.dumps(pending_data, indent=2), encoding="utf-8")
                ptmp.replace(self._pending_path)

        except Exception as e:
            logger.error(f"Failed to save active threads: {e}")

    def load_active(self) -> tuple[dict[str, Thread], list[PendingMessage]]:
        """Load threads from active.json.

        Returns (threads_dict, pending_list). Returns empty dicts/lists
        if file doesn't exist or is corrupted.
        """
        threads: dict[str, Thread] = {}
        pending: list[PendingMessage] = []

        # Load threads
        if self._active_path.exists():
            try:
                raw = json.loads(self._active_path.read_text(encoding="utf-8"))
                for tid, tdata in raw.get("threads", {}).items():
                    t = Thread.from_dict(tdata)
                    threads[tid] = t
                logger.info(
                    f"Loaded {len(threads)} active threads from {self._active_path}"
                )
            except Exception as e:
                logger.error(f"Failed to load active threads: {e}")

        # Load pending
        if self._pending_path.exists():
            try:
                raw = json.loads(self._pending_path.read_text(encoding="utf-8"))
                for pdata in raw.get("messages", []):
                    from .models import ThreadMessage

                    msg = ThreadMessage.from_dict(pdata["message"])
                    pm = PendingMessage(
                        message=msg,
                        channel_key=pdata["channel_key"],
                        created_at=pdata.get("created_at", 0.0),
                        discord_msg_id=msg.discord_msg_id,
                    )
                    pending.append(pm)
                logger.info(f"Loaded {len(pending)} pending messages")
            except Exception as e:
                logger.error(f"Failed to load pending messages: {e}")

        return threads, pending

    # ── Archival ─────────────────────────────────────────────────────

    def archive_thread(self, thread: Thread) -> None:
        """Append a dead thread to the daily JSONL archive.

        File: archive/YYYY-MM-DD.jsonl
        """
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            archive_path = self._archive_dir / f"{date_str}.jsonl"
            line = json.dumps(thread.to_dict(), separators=(",", ":"))
            with open(archive_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.error(f"Failed to archive thread {thread.thread_id}: {e}")

    def archive_threads(self, threads: list[Thread]) -> int:
        """Archive multiple dead threads. Returns count archived."""
        count = 0
        for t in threads:
            self.archive_thread(t)
            count += 1
        return count

    # ── Cleanup ──────────────────────────────────────────────────────

    def delete_old_archives(self, keep_days: int = 30) -> int:
        """Delete archive files older than keep_days. Returns count deleted."""
        if not self._archive_dir.exists():
            return 0

        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=keep_days)
        deleted = 0

        for path in self._archive_dir.glob("*.jsonl"):
            try:
                date_str = path.stem  # "2026-02-18"
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    path.unlink()
                    deleted += 1
            except (ValueError, OSError) as e:
                logger.warning(f"Could not process archive {path}: {e}")

        if deleted:
            logger.info(f"Deleted {deleted} old thread archives (>{keep_days} days)")
        return deleted
