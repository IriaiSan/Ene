"""Lab harness — orchestrator for isolated Ene test instances.

Creates isolated state, wires MockChannel + RecordReplayProvider + AgentLoop,
runs scripted message sequences, and collects results.

The key design principle: the agent in the lab MUST run the same code paths
as production. We change only three seams — data paths, LLM provider,
channel adapter — everything else runs unmodified.

Usage:
    lab = LabHarness(
        run_name="prompt_test_001",
        snapshot_name="live_feb19",
        model="deepseek/deepseek-chat-v3-0324:free",
    )
    await lab.start()
    response = await lab.inject("hello ene!", sender_id="user_1")
    results = await lab.run_script([...])
    state = lab.get_state()
    await lab.stop()
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.mock import MockChannel
from nanobot.lab.state import (
    LabPaths,
    create_run,
    get_cache_dir,
)
from nanobot.providers.base import LLMProvider
from nanobot.providers.record_replay import CacheMode, RecordReplayProvider
from nanobot.session.manager import SessionManager
from nanobot.utils.helpers import set_data_path


@dataclass
class LabConfig:
    """Configuration for a lab instance."""
    run_name: str                          # Unique name for this run
    snapshot_name: str | None = None       # Restore from snapshot (or None for fresh)
    model: str = "deepseek/deepseek-chat-v3-0324:free"  # LLM model
    cache_mode: CacheMode = "replay_or_live"  # RecordReplay mode
    cache_dir: Path | None = None          # Custom cache dir (default: shared lab cache)
    response_timeout: float = 60.0         # Seconds to wait for LLM response
    observatory_enabled: bool = False      # Dashboard on/off (off by default in lab)
    dashboard_port: int = 18792            # Port (avoid conflicts with live)
    temperature: float = 0.7
    max_tokens: int = 4096
    max_iterations: int = 20


@dataclass
class ScriptMessage:
    """A single message in a lab script."""
    sender_id: str
    content: str
    chat_id: str = "lab_general"
    display_name: str | None = None
    username: str | None = None
    reply_to_message_id: str | None = None
    is_reply_to_ene: bool = False
    guild_id: str | None = "lab_guild"
    expect_response: bool = True  # Whether to wait for a response


@dataclass
class ScriptResult:
    """Result of a single script message."""
    input: ScriptMessage
    response: OutboundMessage | None = None
    all_responses: list[OutboundMessage] = field(default_factory=list)
    elapsed_ms: float = 0.0


class LabHarness:
    """Orchestrator for an isolated Ene lab instance.

    Wires all components (state, provider, channel, agent loop) and
    provides methods for message injection and scripted testing.
    """

    def __init__(
        self,
        config: LabConfig | None = None,
        *,
        # Convenience kwargs (alternative to passing LabConfig)
        run_name: str = "lab_run",
        snapshot_name: str | None = None,
        model: str | None = None,
        cache_mode: CacheMode | None = None,
        provider: LLMProvider | None = None,
    ):
        if config:
            self._config = config
        else:
            self._config = LabConfig(
                run_name=run_name,
                snapshot_name=snapshot_name,
                model=model or "deepseek/deepseek-chat-v3-0324:free",
                cache_mode=cache_mode or "replay_or_live",
            )

        self._external_provider = provider  # Optional pre-built provider
        self._paths: LabPaths | None = None
        self._bus: MessageBus | None = None
        self._mock: MockChannel | None = None
        self._agent_loop: Any = None  # AgentLoop (imported lazily to avoid circular)
        self._agent_task: asyncio.Task | None = None
        self._dispatch_task: asyncio.Task | None = None
        self._provider: LLMProvider | None = None
        self._previous_data_path: Path | None = None
        self._started = False

    @property
    def paths(self) -> LabPaths:
        if self._paths is None:
            raise RuntimeError("Lab not started — call start() first")
        return self._paths

    @property
    def mock(self) -> MockChannel:
        if self._mock is None:
            raise RuntimeError("Lab not started — call start() first")
        return self._mock

    @property
    def bus(self) -> MessageBus:
        if self._bus is None:
            raise RuntimeError("Lab not started — call start() first")
        return self._bus

    async def start(self) -> None:
        """Create isolated state, wire all components, start agent loop."""
        if self._started:
            raise RuntimeError("Lab already started")

        # 1. Create isolated state
        self._paths = create_run(
            self._config.run_name,
            snapshot_name=self._config.snapshot_name,
        )

        # 2. Redirect data path helpers to lab instance
        set_data_path(self._paths.data_dir)

        # 3. Build Config programmatically
        nanobot_config = self._build_config()

        # 4. Set up provider (RecordReplay wrapper or external)
        self._provider = self._build_provider(nanobot_config)

        # 5. Create bus + MockChannel
        self._bus = MessageBus()
        self._mock = MockChannel(bus=self._bus)
        await self._mock.start()

        # 6. Create SessionManager with isolated sessions dir
        session_manager = SessionManager(
            self._paths.workspace,
            sessions_dir=self._paths.sessions,
        )

        # 7. Create AgentLoop — same code as production
        from nanobot.agent.loop import AgentLoop

        self._agent_loop = AgentLoop(
            bus=self._bus,
            provider=self._provider,
            workspace=self._paths.workspace,
            model=self._config.model,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            max_iterations=self._config.max_iterations,
            session_manager=session_manager,
            config=nanobot_config,
        )

        # 8. Start bus dispatcher + agent loop as background tasks
        self._dispatch_task = asyncio.create_task(self._bus.dispatch_outbound())
        self._agent_task = asyncio.create_task(self._agent_loop.run())

        self._started = True
        logger.info(f"Lab '{self._config.run_name}' started (model={self._config.model}, cache={self._config.cache_mode})")

    async def stop(self) -> None:
        """Stop the agent loop and clean up."""
        if not self._started:
            return

        # Stop agent loop
        if self._agent_loop:
            self._agent_loop._running = False
        if self._agent_task:
            self._agent_task.cancel()
            try:
                await self._agent_task
            except (asyncio.CancelledError, Exception):
                pass

        # Stop bus
        if self._bus:
            self._bus.stop()
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except (asyncio.CancelledError, Exception):
                pass

        # Stop mock channel
        if self._mock:
            await self._mock.stop()

        # Restore data path
        set_data_path(None)

        self._started = False
        logger.info(f"Lab '{self._config.run_name}' stopped")

    # ── Message injection ─────────────────────────────────

    async def inject(
        self,
        content: str,
        sender_id: str = "lab_user",
        chat_id: str = "lab_general",
        *,
        display_name: str | None = None,
        username: str | None = None,
        is_reply_to_ene: bool = False,
        reply_to_message_id: str | None = None,
        guild_id: str | None = "lab_guild",
        timeout: float | None = None,
    ) -> OutboundMessage | None:
        """Send one message and wait for response.

        Args:
            content: Message text.
            sender_id: Sender platform ID.
            chat_id: Chat/channel ID.
            timeout: Seconds to wait for response (default: config.response_timeout).

        Returns:
            The OutboundMessage response, or None on timeout.
        """
        self.mock.clear_responses()

        await self.mock.inject_message(
            content=content,
            sender_id=sender_id,
            chat_id=chat_id,
            display_name=display_name or sender_id,
            username=username or sender_id,
            is_reply_to_ene=is_reply_to_ene,
            reply_to_message_id=reply_to_message_id,
            guild_id=guild_id,
        )

        return await self.mock.wait_for_response(
            timeout=timeout or self._config.response_timeout
        )

    async def run_script(
        self,
        messages: list[dict[str, Any] | ScriptMessage],
        delay: float = 0.1,
    ) -> list[ScriptResult]:
        """Run a scripted message sequence.

        Each dict should have at minimum:
            sender_id, content
        Optional fields:
            chat_id, display_name, username, is_reply_to_ene,
            reply_to_message_id, guild_id, expect_response

        Special directives (dict keys starting with _):
            _delay: float — pause N seconds before next message
            _verify: dict — state assertion (not executed here, stored in result)

        Args:
            messages: List of message dicts or ScriptMessage objects.
            delay: Seconds between messages.

        Returns:
            List of ScriptResult for each message.
        """
        import time

        results: list[ScriptResult] = []

        for msg_data in messages:
            # Handle special directives
            if isinstance(msg_data, dict):
                if "_delay" in msg_data:
                    await asyncio.sleep(msg_data["_delay"])
                    continue
                if "_verify" in msg_data:
                    # Store for post-processing by stress/audit systems
                    continue

            # Convert dict to ScriptMessage
            if isinstance(msg_data, dict):
                script_msg = ScriptMessage(
                    sender_id=msg_data["sender_id"],
                    content=msg_data["content"],
                    chat_id=msg_data.get("chat_id", "lab_general"),
                    display_name=msg_data.get("display_name"),
                    username=msg_data.get("username"),
                    reply_to_message_id=msg_data.get("reply_to_message_id"),
                    is_reply_to_ene=msg_data.get("is_reply_to_ene", False),
                    guild_id=msg_data.get("guild_id", "lab_guild"),
                    expect_response=msg_data.get("expect_response", True),
                )
            else:
                script_msg = msg_data

            self.mock.clear_responses()
            start = time.monotonic()

            await self.mock.inject_message(
                content=script_msg.content,
                sender_id=script_msg.sender_id,
                chat_id=script_msg.chat_id,
                display_name=script_msg.display_name or script_msg.sender_id,
                username=script_msg.username or script_msg.sender_id,
                is_reply_to_ene=script_msg.is_reply_to_ene,
                reply_to_message_id=script_msg.reply_to_message_id,
                guild_id=script_msg.guild_id,
            )

            response = None
            if script_msg.expect_response:
                response = await self.mock.wait_for_response(
                    timeout=self._config.response_timeout
                )

            elapsed = (time.monotonic() - start) * 1000

            result = ScriptResult(
                input=script_msg,
                response=response,
                all_responses=self.mock.get_responses(),
                elapsed_ms=elapsed,
            )
            results.append(result)

            if delay > 0:
                await asyncio.sleep(delay)

        return results

    def get_state(self) -> dict[str, Any]:
        """Snapshot current state for verification.

        Returns dict with core memory, social profiles, threads, etc.
        """
        import json

        state: dict[str, Any] = {}

        # Core memory
        core_path = self.paths.workspace / "memory" / "core.json"
        if core_path.exists():
            try:
                state["core_memory"] = json.loads(
                    core_path.read_text(encoding="utf-8")
                )
            except Exception:
                state["core_memory"] = None

        # Social profiles
        social_dir = self.paths.workspace / "memory" / "social" / "people"
        if social_dir.exists():
            profiles = {}
            for f in social_dir.glob("*.json"):
                try:
                    profiles[f.stem] = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    pass
            state["social_profiles"] = profiles

        # Social index
        index_path = self.paths.workspace / "memory" / "social" / "index.json"
        if index_path.exists():
            try:
                state["social_index"] = json.loads(
                    index_path.read_text(encoding="utf-8")
                )
            except Exception:
                state["social_index"] = None

        # Active threads
        threads_path = self.paths.workspace / "memory" / "threads" / "active.json"
        if threads_path.exists():
            try:
                state["threads"] = json.loads(
                    threads_path.read_text(encoding="utf-8")
                )
            except Exception:
                state["threads"] = None

        # Sessions
        sessions = {}
        if self.paths.sessions.exists():
            for f in self.paths.sessions.glob("*.jsonl"):
                try:
                    lines = f.read_text(encoding="utf-8").strip().split("\n")
                    sessions[f.stem] = len(lines)
                except Exception:
                    pass
        state["sessions"] = sessions

        # Diary
        diary_dir = self.paths.workspace / "memory" / "diary"
        if diary_dir.exists():
            state["diary_files"] = [f.name for f in sorted(diary_dir.glob("*.md"))]

        return state

    # ── Provider stats ────────────────────────────────────

    def get_provider_stats(self) -> dict[str, int] | None:
        """Get RecordReplayProvider cache stats, or None if not using R/R."""
        if isinstance(self._provider, RecordReplayProvider):
            return self._provider.stats.as_dict()
        return None

    # ── Internal helpers ──────────────────────────────────

    def _build_config(self) -> Any:
        """Build a nanobot Config object programmatically for the lab."""
        from nanobot.config.schema import (
            Config,
            AgentsConfig,
            AgentDefaults,
            ObservatoryConfig,
            MemoryConfig,
        )

        observatory = ObservatoryConfig(
            enabled=self._config.observatory_enabled,
            dashboard_port=self._config.dashboard_port,
            dashboard_enabled=self._config.observatory_enabled,
            db_path=str(self._paths.observatory_db),
        )

        memory = MemoryConfig(
            chroma_path=str(self._paths.chroma_path),
        )

        defaults = AgentDefaults(
            workspace=str(self._paths.workspace),
            model=self._config.model,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            max_tool_iterations=self._config.max_iterations,
            observatory=observatory,
            memory=memory,
        )

        return Config(
            agents=AgentsConfig(defaults=defaults),
        )

    def _build_provider(self, config: Any) -> LLMProvider:
        """Build the LLM provider, optionally wrapped in RecordReplay."""
        if self._external_provider:
            real = self._external_provider
        else:
            # Build from config (same as production)
            from nanobot.providers.litellm_provider import LiteLLMProvider

            api_key = config.get_api_key(self._config.model)
            api_base = config.get_api_base(self._config.model)

            if not api_key:
                # Try environment variables
                import os
                api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")

            real = LiteLLMProvider(
                api_key=api_key,
                api_base=api_base,
                default_model=self._config.model,
                provider_name=config.get_provider_name(self._config.model),
            )

        # Wrap in RecordReplay unless passthrough
        if self._config.cache_mode != "passthrough":
            cache_dir = self._config.cache_dir or get_cache_dir()
            return RecordReplayProvider(
                real_provider=real,
                cache_dir=cache_dir,
                mode=self._config.cache_mode,
            )

        return real
