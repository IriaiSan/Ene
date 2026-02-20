"""Lab state management — snapshots, runs, and state isolation.

Manages named snapshots, run instances, and identity seeding for the
development lab. Follows industry consensus:

- Snapshots are named, immutable, and versioned (Letta Agent Files, LangGraph)
- Never test against live directly (Anthropic eval guide)
- Each trial starts from the same snapshot (tau-bench)
- Snapshots can be created from live OR from a lab run

Storage layout:
    ~/.nanobot-lab/
    ├── _snapshots/              # Named, immutable snapshots
    │   └── {name}/
    │       ├── workspace/       # Full copy of workspace
    │       ├── sessions/        # Full copy of sessions
    │       └── manifest.json    # Metadata
    ├── runs/                    # Active/completed test runs
    │   └── {name}/
    │       ├── workspace/
    │       ├── sessions/
    │       ├── chroma_db/
    │       ├── observatory.db
    │       └── audit/
    └── cache/                   # Shared RecordReplay cache
        └── llm_responses/
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from loguru import logger

from nanobot.utils.helpers import get_data_path


# ── Default lab root ──────────────────────────────────────

_LAB_ROOT_OVERRIDE: Path | None = None


def set_lab_root(path: Path | None) -> None:
    """Override the lab root directory (for testing the lab itself)."""
    global _LAB_ROOT_OVERRIDE
    _LAB_ROOT_OVERRIDE = path


def get_lab_root() -> Path:
    """Get the lab root directory (~/.nanobot-lab by default)."""
    if _LAB_ROOT_OVERRIDE is not None:
        return _LAB_ROOT_OVERRIDE
    return Path.home() / ".nanobot-lab"


# ── Path bundle ───────────────────────────────────────────


class LabPaths(NamedTuple):
    """All paths for an isolated lab instance."""
    workspace: Path       # Memory, diary, threads, social, SOUL.md
    sessions: Path        # JSONL session files
    data_dir: Path        # Parent (~/.nanobot-lab/runs/{name})
    chroma_path: Path     # ChromaDB database
    observatory_db: Path  # SQLite metrics
    audit_dir: Path       # Audit trail JSONL


# ── Snapshot management ───────────────────────────────────


def _snapshots_dir() -> Path:
    return get_lab_root() / "_snapshots"


def _runs_dir() -> Path:
    return get_lab_root() / "runs"


def _cache_dir() -> Path:
    return get_lab_root() / "cache" / "llm_responses"


def create_snapshot(
    name: str,
    source: str = "live",
) -> Path:
    """Create a named snapshot from live state or a lab run.

    Args:
        name: Snapshot name (alphanumeric + underscores).
        source: "live" to snapshot from live Ene,
                "run:<run_name>" to snapshot from a lab run.

    Returns:
        Path to the created snapshot directory.

    Raises:
        ValueError: If snapshot name already exists or source is invalid.
        FileNotFoundError: If source doesn't exist.
    """
    snap_dir = _snapshots_dir() / name
    if snap_dir.exists():
        raise ValueError(f"Snapshot '{name}' already exists")

    if source == "live":
        src_workspace = get_data_path() / "workspace"
        src_sessions = get_data_path() / "sessions"
    elif source.startswith("run:"):
        run_name = source[4:]
        run_dir = _runs_dir() / run_name
        if not run_dir.exists():
            raise FileNotFoundError(f"Run '{run_name}' not found")
        src_workspace = run_dir / "workspace"
        src_sessions = run_dir / "sessions"
    else:
        raise ValueError(f"Invalid source: {source!r} (expected 'live' or 'run:<name>')")

    if not src_workspace.exists():
        raise FileNotFoundError(f"Source workspace not found: {src_workspace}")

    snap_dir.mkdir(parents=True, exist_ok=True)

    # Copy workspace
    dst_workspace = snap_dir / "workspace"
    shutil.copytree(src_workspace, dst_workspace, dirs_exist_ok=True)

    # Copy sessions (may not exist for fresh instances)
    dst_sessions = snap_dir / "sessions"
    if src_sessions.exists():
        shutil.copytree(src_sessions, dst_sessions, dirs_exist_ok=True)
    else:
        dst_sessions.mkdir(parents=True, exist_ok=True)

    # Build manifest
    manifest = _build_manifest(name, source, dst_workspace, dst_sessions)
    (snap_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    logger.info(f"Snapshot '{name}' created from {source} ({manifest['file_count']} files, {manifest['total_size_mb']:.1f} MB)")
    return snap_dir


def list_snapshots() -> list[dict[str, Any]]:
    """List all named snapshots with metadata."""
    snap_root = _snapshots_dir()
    if not snap_root.exists():
        return []

    results: list[dict[str, Any]] = []
    for snap_dir in sorted(snap_root.iterdir()):
        if not snap_dir.is_dir():
            continue
        manifest_path = snap_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                results.append(manifest)
            except Exception as e:
                logger.warning(f"Failed to read manifest for {snap_dir.name}: {e}")
                results.append({"name": snap_dir.name, "error": str(e)})
        else:
            results.append({"name": snap_dir.name, "error": "no manifest"})

    return results


def delete_snapshot(name: str) -> None:
    """Delete a named snapshot."""
    snap_dir = _snapshots_dir() / name
    if not snap_dir.exists():
        raise FileNotFoundError(f"Snapshot '{name}' not found")
    shutil.rmtree(snap_dir)
    logger.info(f"Snapshot '{name}' deleted")


# ── Run management ────────────────────────────────────────


def create_run(
    run_name: str,
    snapshot_name: str | None = None,
) -> LabPaths:
    """Create an isolated run instance.

    Args:
        run_name: Unique name for this run.
        snapshot_name: Restore from this snapshot (or None for fresh empty).

    Returns:
        LabPaths with all paths for this run.

    Raises:
        ValueError: If run name already exists.
        FileNotFoundError: If snapshot doesn't exist.
    """
    run_dir = _runs_dir() / run_name
    if run_dir.exists():
        raise ValueError(f"Run '{run_name}' already exists")

    run_dir.mkdir(parents=True, exist_ok=True)

    paths = LabPaths(
        workspace=run_dir / "workspace",
        sessions=run_dir / "sessions",
        data_dir=run_dir,
        chroma_path=run_dir / "workspace" / "chroma_db",
        observatory_db=run_dir / "workspace" / "observatory.db",
        audit_dir=run_dir / "audit",
    )

    if snapshot_name:
        snap_dir = _snapshots_dir() / snapshot_name
        if not snap_dir.exists():
            raise FileNotFoundError(f"Snapshot '{snapshot_name}' not found")

        # Restore from snapshot
        snap_workspace = snap_dir / "workspace"
        snap_sessions = snap_dir / "sessions"

        if snap_workspace.exists():
            shutil.copytree(snap_workspace, paths.workspace, dirs_exist_ok=True)
        if snap_sessions.exists():
            shutil.copytree(snap_sessions, paths.sessions, dirs_exist_ok=True)

        logger.info(f"Run '{run_name}' created from snapshot '{snapshot_name}'")
    else:
        # Fresh empty instance — create required directory structure
        paths.workspace.mkdir(parents=True, exist_ok=True)
        paths.sessions.mkdir(parents=True, exist_ok=True)
        (paths.workspace / "memory").mkdir(exist_ok=True)
        (paths.workspace / "memory" / "diary").mkdir(parents=True, exist_ok=True)
        (paths.workspace / "memory" / "social").mkdir(parents=True, exist_ok=True)
        (paths.workspace / "memory" / "social" / "people").mkdir(parents=True, exist_ok=True)
        (paths.workspace / "memory" / "threads").mkdir(parents=True, exist_ok=True)
        logger.info(f"Run '{run_name}' created (fresh)")

    # Always ensure audit dir exists
    paths.audit_dir.mkdir(parents=True, exist_ok=True)

    return paths


def list_runs() -> list[dict[str, Any]]:
    """List all runs with metadata."""
    runs_root = _runs_dir()
    if not runs_root.exists():
        return []

    results: list[dict[str, Any]] = []
    for run_dir in sorted(runs_root.iterdir()):
        if not run_dir.is_dir():
            continue

        info: dict[str, Any] = {
            "name": run_dir.name,
            "path": str(run_dir),
        }

        # Check for audit files to determine if run was actually executed
        audit_dir = run_dir / "audit"
        if audit_dir.exists():
            audit_files = list(audit_dir.glob("*.jsonl"))
            info["audit_files"] = len(audit_files)

        # Check workspace existence
        info["has_workspace"] = (run_dir / "workspace").exists()
        info["has_sessions"] = (run_dir / "sessions").exists()

        results.append(info)

    return results


def delete_run(run_name: str) -> None:
    """Delete a run instance and all its state."""
    run_dir = _runs_dir() / run_name
    if not run_dir.exists():
        raise FileNotFoundError(f"Run '{run_name}' not found")
    shutil.rmtree(run_dir)
    logger.info(f"Run '{run_name}' deleted")


def fork_run(source_run: str, count: int) -> list[LabPaths]:
    """Create N isolated copies from an existing run.

    Useful for parallel testing from the same state.

    Args:
        source_run: Name of the run to fork from.
        count: Number of copies to create.

    Returns:
        List of LabPaths, one per fork.
    """
    source_dir = _runs_dir() / source_run
    if not source_dir.exists():
        raise FileNotFoundError(f"Run '{source_run}' not found")

    results: list[LabPaths] = []
    for i in range(count):
        fork_name = f"{source_run}_fork_{i}"
        fork_dir = _runs_dir() / fork_name
        if fork_dir.exists():
            shutil.rmtree(fork_dir)

        shutil.copytree(source_dir, fork_dir)

        paths = LabPaths(
            workspace=fork_dir / "workspace",
            sessions=fork_dir / "sessions",
            data_dir=fork_dir,
            chroma_path=fork_dir / "workspace" / "chroma_db",
            observatory_db=fork_dir / "workspace" / "observatory.db",
            audit_dir=fork_dir / "audit",
        )
        paths.audit_dir.mkdir(parents=True, exist_ok=True)
        results.append(paths)

    logger.info(f"Forked '{source_run}' into {count} copies")
    return results


# ── Identity seeding ──────────────────────────────────────


def seed_identity_files(
    workspace: Path,
    soul_md: str | None = None,
    agents_md: str | None = None,
    user_md: str | None = None,
) -> None:
    """Write identity files into a workspace.

    Use for fresh instances or to override identity for testing.
    Only writes files that are provided (non-None).

    Args:
        workspace: Path to the workspace directory.
        soul_md: Content for SOUL.md.
        agents_md: Content for AGENTS.md.
        user_md: Content for USER.md.
    """
    workspace.mkdir(parents=True, exist_ok=True)

    if soul_md is not None:
        (workspace / "SOUL.md").write_text(soul_md, encoding="utf-8")
    if agents_md is not None:
        (workspace / "AGENTS.md").write_text(agents_md, encoding="utf-8")
    if user_md is not None:
        (workspace / "USER.md").write_text(user_md, encoding="utf-8")


def get_cache_dir() -> Path:
    """Get the shared LLM response cache directory."""
    path = _cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── Manifest builder ──────────────────────────────────────


def _build_manifest(
    name: str,
    source: str,
    workspace: Path,
    sessions: Path,
) -> dict[str, Any]:
    """Build a manifest dict for a snapshot."""
    # Count files and total size
    file_count = 0
    total_size = 0
    for path in workspace.rglob("*"):
        if path.is_file():
            file_count += 1
            total_size += path.stat().st_size

    if sessions.exists():
        for path in sessions.rglob("*"):
            if path.is_file():
                file_count += 1
                total_size += path.stat().st_size

    # Detect contents
    contents: dict[str, Any] = {}
    contents["core_memory"] = (workspace / "memory" / "core.json").exists()

    social_people = workspace / "memory" / "social" / "people"
    if social_people.exists():
        contents["social_profiles"] = len(list(social_people.glob("*.json")))
    else:
        contents["social_profiles"] = 0

    diary_dir = workspace / "memory" / "diary"
    if diary_dir.exists():
        contents["diary_entries"] = len(list(diary_dir.glob("*.md")))
    else:
        contents["diary_entries"] = 0

    if sessions.exists():
        contents["session_files"] = len(list(sessions.glob("*.jsonl")))
    else:
        contents["session_files"] = 0

    contents["chroma_db"] = (workspace / "chroma_db").exists()
    contents["observatory_db"] = (workspace / "observatory.db").exists()

    identity_files = []
    for fname in ("SOUL.md", "AGENTS.md", "USER.md", "what_not_to_do_ever.md"):
        if (workspace / fname).exists():
            identity_files.append(fname)
    contents["identity_files"] = identity_files

    return {
        "name": name,
        "created_at": datetime.now().isoformat(),
        "source": source,
        "file_count": file_count,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "contents": contents,
    }
