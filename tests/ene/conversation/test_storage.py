"""Tests for conversation thread storage."""

import json
import time
import pytest
from pathlib import Path

from nanobot.ene.conversation.models import Thread, ThreadMessage, PendingMessage
from nanobot.ene.conversation.storage import ThreadStorage


# ── Helpers ──────────────────────────────────────────────────────────────


def make_msg(
    msg_id: str = "111",
    author: str = "TestUser",
    content: str = "hello",
    author_id: str = "discord:123",
) -> ThreadMessage:
    return ThreadMessage(
        discord_msg_id=msg_id,
        author_name=author,
        author_username=author.lower(),
        author_id=author_id,
        content=content,
        timestamp=time.time(),
        reply_to_msg_id=None,
        is_reply_to_ene=False,
        classification="respond",
    )


def make_thread(channel: str = "discord:chan1") -> Thread:
    t = Thread.new(channel)
    t.add_message(make_msg(msg_id="m1"))
    t.add_message(make_msg(msg_id="m2", author_id="discord:456"))
    t.topic_keywords = ["test", "hello"]
    return t


# ── Tests ────────────────────────────────────────────────────────────────


class TestThreadStorage:
    def test_ensure_dirs(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()
        assert (tmp_path / "threads").is_dir()
        assert (tmp_path / "threads" / "archive").is_dir()

    def test_save_and_load_active(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()

        t1 = make_thread("discord:chan1")
        t2 = make_thread("discord:chan2")
        threads = {t1.thread_id: t1, t2.thread_id: t2}

        storage.save_active(threads)
        loaded, pending = storage.load_active()

        assert len(loaded) == 2
        assert t1.thread_id in loaded
        assert t2.thread_id in loaded
        assert loaded[t1.thread_id].channel_key == "discord:chan1"
        assert len(loaded[t1.thread_id].messages) == 2

    def test_save_and_load_with_pending(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()

        t1 = make_thread()
        threads = {t1.thread_id: t1}
        pending = [
            PendingMessage(
                message=make_msg(msg_id="p1"),
                channel_key="discord:chan1",
            )
        ]

        storage.save_active(threads, pending)
        loaded_threads, loaded_pending = storage.load_active()

        assert len(loaded_threads) == 1
        assert len(loaded_pending) == 1
        assert loaded_pending[0].discord_msg_id == "p1"

    def test_load_empty(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()
        threads, pending = storage.load_active()
        assert threads == {}
        assert pending == []

    def test_load_corrupted(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()
        # Write garbage
        (tmp_path / "threads" / "active.json").write_text("not json{{{")
        threads, pending = storage.load_active()
        assert threads == {}

    def test_atomic_write(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()

        t1 = make_thread()
        storage.save_active({t1.thread_id: t1})

        # Verify no .tmp file left
        assert not (tmp_path / "threads" / "active.tmp").exists()
        assert (tmp_path / "threads" / "active.json").exists()

    def test_archive_thread(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()

        t = make_thread()
        storage.archive_thread(t)

        # Check JSONL file exists
        archive_files = list((tmp_path / "threads" / "archive").glob("*.jsonl"))
        assert len(archive_files) == 1

        # Verify content
        content = archive_files[0].read_text()
        data = json.loads(content.strip())
        assert data["thread_id"] == t.thread_id

    def test_archive_multiple_threads(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()

        threads = [make_thread() for _ in range(3)]
        count = storage.archive_threads(threads)

        assert count == 3
        archive_files = list((tmp_path / "threads" / "archive").glob("*.jsonl"))
        lines = archive_files[0].read_text().strip().split("\n")
        assert len(lines) == 3

    def test_delete_old_archives(self, tmp_path: Path):
        storage = ThreadStorage(tmp_path / "threads")
        storage.ensure_dirs()

        # Create old archive
        old_path = tmp_path / "threads" / "archive" / "2020-01-01.jsonl"
        old_path.write_text('{"thread_id":"old"}\n')

        # Create recent archive
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        new_path = tmp_path / "threads" / "archive" / f"{today}.jsonl"
        new_path.write_text('{"thread_id":"new"}\n')

        deleted = storage.delete_old_archives(keep_days=30)
        assert deleted == 1
        assert not old_path.exists()
        assert new_path.exists()
