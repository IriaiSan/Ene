"""Agent loop: the core processing engine."""

import asyncio
import time as _time
from contextlib import AsyncExitStack
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.debug_trace import DebugTrace
from nanobot.agent.live_trace import LiveTracer
from nanobot.agent.batch_processor import BatchProcessor
from nanobot.agent.message_processor import MessageProcessor
from nanobot.agent.memory_consolidator import MemoryConsolidator
from nanobot.agent.debounce_manager import DebounceManager
from nanobot.agent.state_inspector import StateInspector
from nanobot.session.manager import Session, SessionManager
from nanobot.ene import EneContext, ModuleRegistry


# === Ene: Security, cleaning, and merging — extracted modules (WHITELIST S1, S3) ===
from nanobot.agent.security import (
    DAD_IDS,
    RESTRICTED_TOOLS,
    ENE_PATTERN,
    is_rate_limited,
    is_muted,
    record_suspicious,
    check_auto_mute,
    MuteUserTool,
)
from nanobot.agent.response_cleaning import clean_response
from nanobot.agent.prompts.loader import PromptLoader
from nanobot.agent.message_merging import (
    format_author,
    classify_message,
    merge_messages_tiered,
    merge_messages,
)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        consolidation_model: str | None = None,
        diary_context_days: int = 3,
        config: Any = None,  # Ene: full Config object for module initialization
    ):
        from nanobot.config.schema import ExecToolConfig
        from nanobot.cron.service import CronService
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.consolidation_model = consolidation_model
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self._config = config

        self.memory = MemoryStore(workspace, diary_context_days=diary_context_days)
        if self.memory.migrate_legacy():
            logger.info("Migrated legacy memory files (MEMORY.md/HISTORY.md) to new architecture")

        # Ene: Module registry must be created before ContextBuilder (which uses it)
        self.module_registry = ModuleRegistry()

        self.context = ContextBuilder(workspace, module_registry=self.module_registry)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._current_caller_id = ""  # Ene: tracks who triggered current processing
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._last_message_time = 0.0  # Ene: for idle tracking
        self._idle_watcher_task: asyncio.Task | None = None
        self._session_summaries: dict[str, str] = {}  # Ene: running summaries per session key
        self._summary_msg_counters: dict[str, int] = {}  # Ene: messages since last summary regen (throttle)
        self._reanchor_interval = 6  # Ene: re-inject identity every N assistant messages (lowered from 10 for anti-injection)
        self._log_dir = workspace / "memory" / "logs"  # Ene: debug trace log directory
        self._trace: DebugTrace | None = None  # Ene: current debug trace (per message)
        self._live = LiveTracer()  # Ene: real-time event tracer for live dashboard
        self._batch_processor = BatchProcessor(self)  # Ene: extracted batch pipeline (Phase 3)
        self._message_processor = MessageProcessor(self)  # Ene: extracted message pipeline (Phase 4)
        self._memory_consolidator = MemoryConsolidator(self)  # Ene: extracted memory consolidation (Phase 5)
        self._debounce_manager = DebounceManager(self)  # Ene: extracted debounce/queue (Phase 6)
        self._state_inspector = StateInspector(self)  # Ene: extracted state/control (Phase 6)

        # Ene: message debounce + queue — batch messages, process sequentially
        self._debounce_window = 3.5  # seconds quiet before flushing batch to queue (was 2.0)
        self._debounce_batch_limit = 15  # force-flush when batch reaches this size (was 10)
        self._debounce_buffers: dict[str, list[InboundMessage]] = {}  # channel_key -> intake buffer
        self._debounce_timers: dict[str, asyncio.Task] = {}  # channel_key -> timer task
        self._debounce_max_buffer = 40  # hard cap on intake buffer, drops oldest (was 20)
        self._channel_queues: dict[str, list[list[InboundMessage]]] = {}  # channel_key -> [batches]
        self._queue_processors: dict[str, asyncio.Task] = {}  # channel_key -> processor task
        self._queue_merge_cap = 30  # max messages in a merged batch (keeps newest, drops oldest)

        # Ene: per-user rate limiting — prevents spam attacks
        self._user_message_timestamps: dict[str, list[float]] = {}  # user_id -> [timestamps]
        self._rate_limit_window = 30.0  # seconds
        self._rate_limit_max = 10  # max messages per window for non-Dad users

        # Ene: brain toggle — decouple Discord/dashboard from LLM response generation
        # When OFF: Discord stays connected, dashboard works, messages are observed, but no LLM calls fire
        self._brain_enabled: bool = True

        # Ene: mute system — temporarily ignore persistent spammers/jailbreakers
        self._muted_users: dict[str, float] = {}  # caller_id -> mute_expires_at (unix timestamp)
        self._user_jailbreak_scores: dict[str, list[float]] = {}  # caller_id -> [timestamps of suspicious msgs]
        self._mute_duration = 600  # 10 minutes
        self._jailbreak_window = 300  # 5-minute window for counting suspicious msgs
        self._jailbreak_threshold = 3  # suspicious msgs in window to trigger mute

        self._observatory_module = None  # Set in _register_ene_modules if available
        self._current_inbound_msg = None  # Ene: current message being processed (for message tool cleaning)
        self._last_message_content: str | None = None  # Ene: actual content sent via message tool (for session storage)
        self._module_metrics: dict = {}  # Ene: ModuleMetrics instances keyed by module name
        self._batch_counter = 0  # Ene: monotonic batch counter for trace_id generation
        self._prompts = PromptLoader()  # Ene: centralized prompt loader for version tracking
        self._register_default_tools()
        self._register_ene_modules()

    @property
    def _observatory(self):
        """Quick access to the observatory collector (or None)."""
        if self._observatory_module and self._observatory_module.collector:
            return self._observatory_module.collector
        return None

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))
        
        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))
        
        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        
        # Message tool — wrapped callback applies _ene_clean_response() before sending
        # Without this, message tool bypasses all length limits, reflection stripping, etc.
        async def _cleaned_message_send(outbound: OutboundMessage) -> None:
            if self._current_inbound_msg:
                # Resolve #msgN tag to real Discord message ID for reply threading
                if outbound.reply_to and outbound.reply_to.startswith("#msg"):
                    msg_id_map = self._current_inbound_msg.metadata.get("msg_id_map", {})
                    real_id = msg_id_map.get(outbound.reply_to)
                    if real_id:
                        outbound.reply_to = real_id
                    else:
                        # Tag not found — fall back to no reply threading
                        outbound.reply_to = None

                cleaned = self._ene_clean_response(outbound.content, self._current_inbound_msg)
                if cleaned:
                    outbound.content = cleaned
                    self._last_message_content = cleaned  # Capture for session storage
                    # Inject Ene's response into thread for full conversation tracking
                    _conv = self.module_registry.get_module("conversation_tracker")
                    if _conv and hasattr(_conv, "tracker") and _conv.tracker:
                        _conv.tracker.add_ene_response(self._current_inbound_msg, cleaned)
                    await self.bus.publish_outbound(outbound)
                else:
                    logger.debug("Message tool output cleaned to empty, not sending")
            else:
                await self.bus.publish_outbound(outbound)

        message_tool = MessageTool(send_callback=_cleaned_message_send)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
        
        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

        # Ene: mute tool — lets Ene mute annoying users (NOT in RESTRICTED_TOOLS)
        mute_tool = MuteUserTool(self._muted_users, self.module_registry)
        self.tools.register(mute_tool)

        # Note: Memory tools (save, edit, delete, search) are now registered
        # via the ModuleRegistry in _register_ene_modules()
    
    def _register_ene_modules(self) -> None:
        """Register Ene subsystem modules (memory, personality, etc.).

        Modules are created and registered here but initialized lazily
        in run() since initialization is async (needs provider, workspace, etc.).
        """
        try:
            from nanobot.ene.memory import MemoryModule

            cfg = self._config
            if cfg:
                mem_cfg = cfg.agents.defaults.memory
                mem_module = MemoryModule(
                    token_budget=mem_cfg.core_token_budget,
                    chroma_path=mem_cfg.chroma_path or None,
                    embedding_model=mem_cfg.embedding_model,
                    idle_trigger_seconds=mem_cfg.idle_trigger_seconds,
                    diary_context_days=mem_cfg.diary_context_days,
                )
            else:
                mem_module = MemoryModule()

            self.module_registry.register(mem_module)

            # Register Social Module (Module 2: people, trust, social graph)
            from nanobot.ene.social import SocialModule
            social_module = SocialModule()
            self.module_registry.register(social_module)

            # Register Observatory Module (Module 3: metrics, monitoring, experiments)
            from nanobot.ene.observatory import ObservatoryModule
            self._observatory_module = ObservatoryModule()
            self.module_registry.register(self._observatory_module)

            # Watchdog Module (Module 4) — DISABLED for now to save costs.
            # TODO: re-enable when free model rotation is battle-tested.
            # from nanobot.ene.watchdog import WatchdogModule
            # watchdog_module = WatchdogModule()
            # self.module_registry.register(watchdog_module)

            # Register Conversation Tracker Module (Module 5: thread detection)
            from nanobot.ene.conversation import ConversationTrackerModule
            self.module_registry.register(ConversationTrackerModule())

            # Register Daemon Module (Module 6: subconscious pre-processor)
            from nanobot.ene.daemon import DaemonModule
            self.module_registry.register(DaemonModule())

        except Exception as e:
            logger.error(f"Failed to register Ene modules: {e}", exc_info=True)

    async def _initialize_ene_modules(self) -> None:
        """Initialize all registered Ene modules (async).

        Called once from run() before the main loop starts.
        Creates the EneContext and calls initialize_all().
        """
        if not self.module_registry.modules:
            return

        ctx = EneContext(
            workspace=self.workspace,
            provider=self.provider,
            config=self._config,
            bus=self.bus,
            sessions=self.sessions,
        )
        await self.module_registry.initialize_all(ctx)

        # Wire conversation tracker → social module for name resolution
        conv_mod = self.module_registry.get_module("conversation_tracker")
        social_mod = self.module_registry.get_module("social")
        if conv_mod and hasattr(conv_mod, "tracker") and conv_mod.tracker and social_mod:
            registry = getattr(social_mod, "registry", None)
            if registry:
                conv_mod.tracker.set_social_registry(registry)

        # Wire observatory collector to watchdog + daemon for cost tracking
        obs_mod = self.module_registry.get_module("observatory")
        if obs_mod:
            collector = getattr(obs_mod, "collector", None)
            if collector:
                # Watchdog wiring disabled (module disabled to save costs)
                # Wire to daemon processor
                daemon_mod = self.module_registry.get_module("daemon")
                if daemon_mod and hasattr(daemon_mod, "processor") and daemon_mod.processor:
                    daemon_mod.processor._observatory = collector
                    logger.debug("Wired observatory → daemon")

            # Wire LiveTracer → observatory dashboard for real-time processing view
            if hasattr(obs_mod, "set_live_tracer"):
                obs_mod.set_live_tracer(self._live)
                logger.debug("Wired LiveTracer → observatory")

            # Wire hard-reset callback → dashboard so the Reset button works
            if hasattr(obs_mod, "set_reset_callback"):
                obs_mod.set_reset_callback(self.hard_reset)
                logger.debug("Wired hard_reset → observatory")

            # Wire AgentLoop → ControlAPI for the control panel
            if hasattr(obs_mod, "set_control_api_loop"):
                obs_mod.set_control_api_loop(self)
                logger.debug("Wired AgentLoop → ControlAPI")

            # Wire ModuleMetrics instances to modules for per-module observability
            store = getattr(obs_mod, "store", None)
            if store:
                from nanobot.ene.observatory.module_metrics import ModuleMetrics

                # Signals (classification scoring)
                from nanobot.ene.conversation import signals as signals_mod
                signals_metrics = ModuleMetrics("signals", store, self._live)
                signals_mod.set_metrics(signals_metrics)

                # Conversation tracker (thread lifecycle + live scoring events)
                from nanobot.ene.conversation import tracker as tracker_mod
                tracker_metrics = ModuleMetrics("tracker", store, self._live)
                tracker_mod.set_metrics(tracker_metrics)
                tracker_mod.set_live_tracer(self._live)

                # Daemon (pre-classification)
                from nanobot.ene.daemon import processor as daemon_proc_mod
                daemon_metrics = ModuleMetrics("daemon", store, self._live)
                daemon_proc_mod.set_metrics(daemon_metrics)

                # Response cleaning
                from nanobot.agent import response_cleaning as cleaning_mod
                cleaning_metrics = ModuleMetrics("cleaning", store, self._live)
                cleaning_mod.set_metrics(cleaning_metrics)

                # Memory / sleep agent
                from nanobot.ene.memory import sleep_agent as sleep_agent_mod
                memory_metrics = ModuleMetrics("memory", store, self._live)
                sleep_agent_mod.set_metrics(memory_metrics)

                # Store references for trace_id propagation
                self._module_metrics = {
                    "signals": signals_metrics,
                    "tracker": tracker_metrics,
                    "daemon": daemon_metrics,
                    "cleaning": cleaning_metrics,
                    "memory": memory_metrics,
                }
                logger.debug("Wired ModuleMetrics → signals, tracker, daemon, cleaning, memory")

        # Register module tools with the ToolRegistry
        for tool in self.module_registry.get_all_tools():
            # Replace old tool if it has the same name (e.g., save_memory)
            if self.tools.has(tool.name):
                self.tools.unregister(tool.name)
            self.tools.register(tool)
            logger.debug(f"Registered module tool: {tool.name}")

    async def _idle_watcher(self) -> None:
        """Background task that checks for idle and broadcasts to modules."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every 60 seconds
                if self._last_message_time > 0:
                    idle = _time.time() - self._last_message_time
                    await self.module_registry.notify_idle(idle)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Idle watcher error: {e}")

    async def _daily_trigger(self) -> None:
        """Background task that triggers daily module maintenance at configured hour."""
        import datetime
        daily_hour = 4  # Default 4 AM
        if self._config:
            daily_hour = getattr(
                self._config.agents.defaults.memory, "daily_trigger_hour", 4
            )

        while self._running:
            try:
                now = datetime.datetime.now()
                # Calculate seconds until next trigger hour
                target = now.replace(hour=daily_hour, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += datetime.timedelta(days=1)
                wait_seconds = (target - now).total_seconds()

                logger.debug(f"Daily trigger: next run at {target.isoformat()}")
                await asyncio.sleep(wait_seconds)

                if self._running:
                    logger.info("Running daily module maintenance")
                    await self.module_registry.notify_daily()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily trigger error: {e}")
                await asyncio.sleep(3600)  # Retry in 1 hour on error

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or not self._mcp_servers:
            return
        self._mcp_connected = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        self._mcp_stack = AsyncExitStack()
        await self._mcp_stack.__aenter__()
        await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)

    def _set_tool_context(self, channel: str, chat_id: str) -> None:
        """Update context for all tools that need routing info."""
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.set_context(channel, chat_id)

        if spawn_tool := self.tools.get("spawn"):
            if isinstance(spawn_tool, SpawnTool):
                spawn_tool.set_context(channel, chat_id)

        if cron_tool := self.tools.get("cron"):
            if isinstance(cron_tool, CronTool):
                cron_tool.set_context(channel, chat_id)

    async def _run_agent_loop(self, initial_messages: list[dict]) -> tuple[str | None, list[str]]:
        """
        Run the agent iteration loop.

        Args:
            initial_messages: Starting messages for the LLM conversation.

        Returns:
            Tuple of (final_content, list_of_tools_used).
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        message_sent = False  # Ene: track if message tool already sent a response
        post_message_turns = 0  # Ene: count turns after message was sent
        consecutive_same_tool = 0  # Ene: loop detection — same tool called repeatedly
        last_tool_name = None

        while iteration < self.max_iterations:
            iteration += 1

            # Ene: non-Dad callers don't see restricted tools at all
            # Saves tokens and prevents "Access denied" weirdness
            tool_defs = self.tools.get_definitions_for_caller(
                self._current_caller_id, DAD_IDS, RESTRICTED_TOOLS
            )

            if self._trace:
                self._trace.log_llm_call(iteration, self.model)

            # Ene: live trace — LLM call
            self._live.emit(
                "llm_call", "",
                iteration=iteration,
                model=self.model,
                message_count=len(messages),
                tool_count=len(tool_defs),
            )

            _obs_start = _time.perf_counter()
            response = await self.provider.chat(
                messages=messages,
                tools=tool_defs,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            if self._observatory:
                self._observatory.record(
                    response, call_type="response", model=self.model,
                    caller_id=self._current_caller_id or "system",
                    latency_start=_obs_start,
                )

            # Ene: live trace — LLM response
            _llm_latency = int((_time.perf_counter() - _obs_start) * 1000)
            _tool_call_names = [tc.name for tc in response.tool_calls] if response.has_tool_calls else []
            self._live.emit(
                "llm_response", "",
                iteration=iteration,
                latency_ms=_llm_latency,
                tool_calls=_tool_call_names,
                content_preview=response.content[:120] if response.content else None,
                has_reasoning=bool(response.reasoning_content),
                reasoning_preview=response.reasoning_content[:200] if response.reasoning_content else None,
            )

            # Ene: prompt log — record Ene's full raw response for this iteration
            _tool_calls_detail = [
                {"name": tc.name, "args": tc.arguments}
                for tc in response.tool_calls
            ] if response.has_tool_calls else []
            self._live.emit_prompt(
                "prompt_ene_response", "",
                iteration=iteration,
                latency_ms=_llm_latency,
                content=response.content or "",
                reasoning_content=response.reasoning_content or "",
                tool_calls=_tool_calls_detail,
            )

            if self._trace:
                self._trace.log_llm_response(response)

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")

                    # Ene: pre-execution guard — block duplicate message tool calls.
                    # The post-execution guard (lines below) catches duplicates too late —
                    # both messages are already sent to Discord by that point.
                    if tool_call.name == "message" and message_sent:
                        logger.warning("Agent loop: blocked duplicate message tool (pre-execution guard)")
                        self._live.emit(
                            "loop_break", "",
                            reason="duplicate_message_blocked",
                            iterations=iteration,
                            tools_used=tools_used,
                        )
                        result = "Error: message already sent this batch, cannot send another."
                    # Ene: restrict dangerous tools to Dad only
                    elif tool_call.name in RESTRICTED_TOOLS and self._current_caller_id not in DAD_IDS:
                        result = "Access denied."
                        logger.warning(f"Blocked restricted tool '{tool_call.name}' for caller {self._current_caller_id}")
                    else:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)

                    # Ene: live trace — tool execution
                    self._live.emit(
                        "tool_exec", "",
                        tool_name=tool_call.name,
                        args_preview=args_str[:150],
                        result_preview=str(result)[:150] if result else None,
                    )

                    if self._trace:
                        self._trace.log_tool_result(tool_call.name, str(result))

                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )

                    # Ene: track message sends
                    if tool_call.name == "message":
                        message_sent = True

                # Ene: general loop detection — same tool called repeatedly
                current_tools = [tc.name for tc in response.tool_calls]
                if len(current_tools) == 1 and current_tools[0] == last_tool_name:
                    consecutive_same_tool += 1
                else:
                    consecutive_same_tool = 0
                last_tool_name = current_tools[-1] if current_tools else None

                if consecutive_same_tool >= 3:
                    logger.warning(f"Agent loop: tool '{last_tool_name}' called {consecutive_same_tool + 1}x in a row, breaking")
                    self._live.emit(
                        "loop_break", "",
                        reason=f"same_tool_repeat ({last_tool_name})",
                        iterations=iteration,
                        tools_used=tools_used,
                    )
                    final_content = None
                    break

                # Ene: once message tool is used, allow max 1 more iteration
                # for memory/person tools, then hard stop. Never send 2 messages.
                if message_sent:
                    if tools_used.count("message") >= 2:
                        logger.warning("Agent loop: duplicate message tool, breaking")
                        self._live.emit(
                            "loop_break", "",
                            reason="duplicate_message_tool",
                            iterations=iteration,
                            tools_used=tools_used,
                        )
                        final_content = None
                        break
                    post_message_turns += 1
                    if post_message_turns > 1:
                        logger.debug("Agent loop: post-message turn limit, stopping")
                        self._live.emit(
                            "loop_break", "",
                            reason="post_message_turn_limit",
                            iterations=iteration,
                            tools_used=tools_used,
                        )
                        final_content = None
                        break

            else:
                final_content = response.content
                # Ene: if message tool already sent a response, suppress ANY
                # follow-up text. The old check only caught "done" / "done."
                # but the LLM often says things like "I've sent the message!"
                # which would leak as a SECOND Discord message.
                if message_sent:
                    final_content = None
                # Ene: if LLM output looks like raw tool call XML, suppress it.
                # DeepSeek sometimes outputs garbled XML as plain text
                # instead of structured tool calls — must not reach Discord.
                # Catches: <function_calls>, <functioninvoke, <invoke, <parameter
                elif final_content and re.search(
                    r'<\s*(?:function|invoke|parameter)', final_content, re.IGNORECASE
                ):
                    logger.warning("Agent loop: raw tool call XML in text response, suppressing")
                    final_content = None

                _break_reason = "message_sent" if message_sent else "natural_end"
                self._live.emit(
                    "loop_break", "",
                    reason=_break_reason,
                    iterations=iteration,
                    tools_used=tools_used,
                )
                break

        return final_content, tools_used

    def _format_author(self, m: InboundMessage) -> str:
        """Delegate to message_merging.format_author (WHITELIST S3)."""
        return format_author(
            m, self._muted_users, self._user_jailbreak_scores,
            module_registry=self.module_registry,
            record_suspicious_fn=lambda cid, reason: record_suspicious(
                self._user_jailbreak_scores, cid, reason, self.module_registry
            ),
        )

    def _classify_message(self, msg: InboundMessage, channel_state=None) -> str:
        """Delegate to message_merging.classify_message (WHITELIST S3)."""
        return classify_message(msg, self._muted_users, channel_state)

    def _merge_messages_tiered(
        self,
        respond_msgs: list[InboundMessage],
        context_msgs: list[InboundMessage],
    ) -> InboundMessage:
        """Delegate to message_merging.merge_messages_tiered (WHITELIST S3)."""
        return merge_messages_tiered(respond_msgs, context_msgs, self._format_author)

    def _merge_messages(self, messages: list[InboundMessage]) -> InboundMessage:
        """Delegate to message_merging.merge_messages (WHITELIST S3)."""
        return merge_messages(messages, self._format_author)

    async def _process_batch(self, channel_key: str, messages: list[InboundMessage]) -> None:
        """Process a batch of messages — classify, merge, and send to LLM.

        Delegates to BatchProcessor (extracted Phase 3). See batch_processor.py
        for the full pipeline: tag stale → rotate session → classify → merge → dispatch.
        """
        await self._batch_processor.process(channel_key, messages)

    def _is_rate_limited(self, msg: "InboundMessage") -> bool:
        """Delegate to security.is_rate_limited (WHITELIST S3)."""
        caller_id = f"{msg.channel}:{msg.sender_id}"
        timestamps = self._user_message_timestamps.get(caller_id, [])
        limited, pruned = is_rate_limited(
            timestamps, self._rate_limit_window, self._rate_limit_max, caller_id,
        )
        self._user_message_timestamps[caller_id] = pruned
        if limited:
            logger.warning(
                f"Rate limited {msg.metadata.get('author_name', msg.sender_id)} "
                f"({len(pruned)} msgs in {self._rate_limit_window}s)"
            )
            self._live.emit(
                "rate_limited", msg.session_key,
                sender=msg.metadata.get("author_name", msg.sender_id),
                count=len(pruned),
            )
            self._record_suspicious(caller_id, "rate limited (spam)")
            self._check_auto_mute(msg)
        return limited

    def _is_muted(self, caller_id: str) -> bool:
        """Delegate to security.is_muted (WHITELIST S3)."""
        return is_muted(self._muted_users, caller_id)

    def _record_suspicious(self, caller_id: str, reason: str = "suspicious behavior") -> None:
        """Delegate to security.record_suspicious (WHITELIST S3)."""
        record_suspicious(self._user_jailbreak_scores, caller_id, reason, self.module_registry)

    def _check_auto_mute(self, msg: "InboundMessage") -> None:
        """Delegate to security.check_auto_mute (WHITELIST S3)."""
        author = msg.metadata.get('author_name', msg.sender_id)
        caller_id = f"{msg.channel}:{msg.sender_id}"
        check_auto_mute(
            self._user_jailbreak_scores, self._muted_users,
            caller_id, author,
            self._jailbreak_window, self._jailbreak_threshold, self._mute_duration,
            module_registry=self.module_registry,
        )

    async def _debounce_timer(self, channel_key: str) -> None:
        """Delegates to DebounceManager (extracted Phase 6)."""
        await self._debounce_manager.debounce_timer(channel_key)

    def _enqueue_batch(self, channel_key: str) -> None:
        """Delegates to DebounceManager (extracted Phase 6)."""
        self._debounce_manager.enqueue_batch(channel_key)

    async def _process_queue(self, channel_key: str) -> None:
        """Delegates to DebounceManager (extracted Phase 6)."""
        await self._debounce_manager.process_queue(channel_key)

    def hard_reset(self) -> None:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        self._state_inspector.hard_reset()

    # ── Model switching ──────────────────────────────────────────────────

    def set_model(self, model: str) -> str:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        return self._state_inspector.set_model(model)

    def get_model(self) -> str:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        return self._state_inspector.get_model()

    # ── Brain toggle ──────────────────────────────────────────────────────

    def pause_brain(self) -> None:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        self._state_inspector.pause_brain()

    def resume_brain(self) -> None:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        self._state_inspector.resume_brain()

    def is_brain_enabled(self) -> bool:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        return self._state_inspector.is_brain_enabled()

    # ── Security state accessors (for control panel) ──────────────────────

    def get_muted_users(self) -> dict[str, float]:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        return self._state_inspector.get_muted_users()

    def get_rate_limit_state(self) -> dict[str, list[float]]:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        return self._state_inspector.get_rate_limit_state()

    def get_jailbreak_scores(self) -> dict[str, list[float]]:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        return self._state_inspector.get_jailbreak_scores()

    def mute_user(self, caller_id: str, duration_min: float = 30.0) -> None:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        self._state_inspector.mute_user(caller_id, duration_min)

    def unmute_user(self, caller_id: str) -> bool:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        return self._state_inspector.unmute_user(caller_id)

    def clear_rate_limit(self, caller_id: str) -> bool:
        """Delegates to StateInspector (extracted Phase 6). See state_inspector.py."""
        return self._state_inspector.clear_rate_limit(caller_id)

    async def _latency_warning(
        self,
        channel: str,
        chat_id: str,
        reply_to: str | None,
        threshold: float = 18.0,
    ) -> None:
        """Wait `threshold` seconds, then send a canned latency notice if not cancelled.

        Designed to be run as a background task alongside _run_agent_loop.
        The caller cancels this task as soon as _run_agent_loop returns, so the
        warning only fires when the LLM is genuinely slow (API lag, not Ene).
        """
        await asyncio.sleep(threshold)
        canned = "having some lag rn, give me a sec 🫥"
        try:
            await self.bus.publish_outbound(OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=canned,
                reply_to=reply_to,
            ))
            logger.info(f"Latency warning sent to {channel}:{chat_id} after {threshold}s")
        except Exception as e:
            logger.debug(f"Latency warning send failed: {e}")

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()

        # Initialize Ene modules (memory, personality, etc.)
        await self._initialize_ene_modules()

        # Start background tasks for idle watching and daily triggers
        self._idle_watcher_task = asyncio.create_task(self._idle_watcher())
        self._daily_trigger_task = asyncio.create_task(self._daily_trigger())

        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                # Ene: live trace — message arrived
                _sender_name = msg.metadata.get("author_name", msg.sender_id) if msg.metadata else msg.sender_id
                _meta_flags = []
                if msg.metadata:
                    if msg.metadata.get("is_reply_to_ene"):
                        _meta_flags.append("reply_to_ene")
                    if msg.metadata.get("guild_id"):
                        _meta_flags.append("guild")
                    else:
                        _meta_flags.append("DM")
                self._live.emit(
                    "msg_arrived", msg.session_key,
                    sender=_sender_name,
                    content_preview=msg.content[:100],
                    metadata_flags=", ".join(_meta_flags) if _meta_flags else None,
                )

                # Ene: brain toggle — observe but don't process when brain is off
                if not self._brain_enabled:
                    self._live.emit(
                        "brain_paused", msg.session_key,
                        sender=_sender_name,
                        content_preview=msg.content[:80] if msg.content else "",
                    )
                    continue

                # Ene: system messages bypass debounce
                if msg.channel == "system":
                    try:
                        response = await self._process_message(msg)
                        if response:
                            await self.bus.publish_outbound(response)
                    except Exception as e:
                        logger.error(f"Error processing system message: {e}", exc_info=True)
                    continue

                # Ene: per-user rate limiting — drop spam before it enters the buffer
                if self._is_rate_limited(msg):
                    continue

                # Ene: debounce + queue — buffer messages, flush on time or count
                channel_key = msg.session_key  # "channel:chat_id"

                if channel_key not in self._debounce_buffers:
                    self._debounce_buffers[channel_key] = []
                self._debounce_buffers[channel_key].append(msg)

                # Ene: live trace — message added to debounce buffer
                self._live.emit(
                    "debounce_add", channel_key,
                    sender=msg.metadata.get("author_name", msg.sender_id) if msg.metadata else msg.sender_id,
                    buffer_size=len(self._debounce_buffers[channel_key]),
                )

                # Hard cap — drop oldest if flooding
                if len(self._debounce_buffers[channel_key]) > self._debounce_max_buffer:
                    dropped = len(self._debounce_buffers[channel_key]) - self._debounce_max_buffer
                    self._debounce_buffers[channel_key] = self._debounce_buffers[channel_key][-self._debounce_max_buffer:]
                    logger.warning(f"Debounce: dropped {dropped} oldest in {channel_key} (buffer cap)")

                # Count-based trigger: force-flush when batch limit reached
                if len(self._debounce_buffers[channel_key]) >= self._debounce_batch_limit:
                    existing = self._debounce_timers.pop(channel_key, None)
                    if existing and not existing.done():
                        existing.cancel()
                    self._enqueue_batch(channel_key)
                else:
                    # Time-based trigger: reset sliding window timer
                    existing = self._debounce_timers.get(channel_key)
                    if existing and not existing.done():
                        existing.cancel()
                    self._debounce_timers[channel_key] = asyncio.create_task(
                        self._debounce_timer(channel_key)
                    )

            except asyncio.TimeoutError:
                continue
    
    async def close_mcp(self) -> None:
        """Close MCP connections and shutdown Ene modules."""
        # Shutdown Ene modules
        if self.module_registry.modules:
            await self.module_registry.shutdown_all()

        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False

        # Cancel background tasks
        if self._idle_watcher_task and not self._idle_watcher_task.done():
            self._idle_watcher_task.cancel()
        if hasattr(self, "_daily_trigger_task") and self._daily_trigger_task and not self._daily_trigger_task.done():
            self._daily_trigger_task.cancel()

        logger.info("Agent loop stopping")
    
    def _ene_clean_response(self, content: str, msg: InboundMessage) -> str | None:
        """Delegate to response_cleaning.clean_response (WHITELIST S3, X2)."""
        is_public = msg.channel == "discord" and msg.metadata.get("guild_id")
        return clean_response(content, msg, is_public=bool(is_public))

    def _should_respond(self, msg: InboundMessage) -> bool:
        """Decide if Ene should respond or just lurk. Ene-specific."""
        caller_id = f"{msg.channel}:{msg.sender_id}"

        # Always respond to Dad
        if caller_id in DAD_IDS:
            return True

        # Always respond in DMs (no guild_id means DM on Discord)
        if msg.channel == "discord" and not msg.metadata.get("guild_id"):
            return True

        # Always respond on non-Discord channels (Telegram is Dad-only anyway)
        if msg.channel != "discord":
            return True

        # Respond if "ene" is mentioned in the message (word-boundary to avoid "scene", "generic", etc.)
        if ENE_PATTERN.search(msg.content):
            return True

        # Ene: respond when someone replies to one of Ene's messages
        if msg.metadata.get("is_reply_to_ene"):
            logger.debug(f"Responding to reply-to-Ene from {msg.sender_id}")
            return True

        # Otherwise lurk — store in session but don't respond
        return False

    def _is_dm(self, msg: InboundMessage) -> bool:
        """Check if a message is a direct message (not in a server/group)."""
        if msg.channel == "discord":
            return msg.metadata.get("guild_id") is None
        if msg.channel == "telegram":
            return not msg.metadata.get("is_group", False)
        return False

    def _dm_access_allowed(self, msg: InboundMessage) -> bool:
        """Check if sender has sufficient trust tier for DM access.

        Only people at 'familiar' or higher can DM Ene.
        Dad always passes. Zero-cost check — no LLM involved.
        """
        from nanobot.ene.social.trust import TIER_ORDER

        DM_MINIMUM_TIER = "familiar"

        # Dad always passes
        caller_id = f"{msg.channel}:{msg.sender_id}"
        if caller_id in DAD_IDS:
            return True

        # Look up person via social module
        social = self.module_registry.get_module("social")
        if social is None or social.registry is None:
            # Social module not loaded — fall back to Dad-only
            return caller_id in DAD_IDS

        person = social.registry.get_by_platform_id(caller_id)
        if person is None:
            return False

        try:
            person_idx = TIER_ORDER.index(person.trust.tier)
            min_idx = TIER_ORDER.index(DM_MINIMUM_TIER)
            return person_idx >= min_idx
        except ValueError:
            return False

    async def _generate_running_summary(self, session: Session, key: str) -> str | None:
        """Generate or update a running summary of older conversation messages.

        Delegates to MemoryConsolidator (extracted Phase 5). See memory_consolidator.py.
        """
        return await self._memory_consolidator.generate_running_summary(session, key)

    def _should_reanchor(self, session: Session) -> bool:
        """Check if identity re-anchoring is needed to prevent persona drift.

        Delegates to MemoryConsolidator (extracted Phase 5). See memory_consolidator.py.
        """
        return self._memory_consolidator.should_reanchor(session)

    async def _process_message(self, msg: InboundMessage, session_key: str | None = None) -> OutboundMessage | None:
        """Process a single inbound message.

        Delegates to MessageProcessor (extracted Phase 4). See message_processor.py
        for the full pipeline: gate → decide → respond → store → clean → output.
        """
        return await self._message_processor.process(msg, session_key)
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        self._set_tool_context(origin_channel, origin_chat_id)
        initial_messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        final_content, _ = await self._run_agent_loop(initial_messages)

        if final_content is None:
            final_content = "Background task completed."
        
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def _consolidate_memory(self, session, archive_all: bool = False) -> None:
        """Consolidate old messages into a diary entry.

        Delegates to MemoryConsolidator (extracted Phase 5). See memory_consolidator.py.
        """
        await self._memory_consolidator.consolidate(session, archive_all=archive_all)

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).
        
        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).
            channel: Source channel (for tool context routing).
            chat_id: Source chat ID (for tool context routing).
        
        Returns:
            The agent's response.
        """
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content
        )
        
        response = await self._process_message(msg, session_key=session_key)
        return response.content if response else ""
