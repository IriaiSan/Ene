"""Agent loop: the core processing engine."""

import asyncio
import time as _time
from contextlib import AsyncExitStack
import json
import json_repair
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
from nanobot.session.manager import Session, SessionManager
from nanobot.ene import EneContext, ModuleRegistry


# === Ene: Hardcoded identity and security ===
DAD_IDS = {"telegram:8559611823", "discord:1175414972482846813"}
RESTRICTED_TOOLS = {"exec", "write_file", "edit_file", "read_file", "list_dir", "spawn", "cron"}


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

        # Ene: Module registry (memory, personality, goals, etc.)
        self.module_registry = ModuleRegistry()
        self._register_default_tools()
        self._register_ene_modules()
    
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
        
        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
        
        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

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

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

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

                    # Ene: restrict dangerous tools to Dad only
                    if tool_call.name in RESTRICTED_TOOLS and self._current_caller_id not in DAD_IDS:
                        result = "Access denied."
                        logger.warning(f"Blocked restricted tool '{tool_call.name}' for caller {self._current_caller_id}")
                    else:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                messages.append({"role": "user", "content": "Reflect on the results and decide next steps."})
            else:
                final_content = response.content
                break

        return final_content, tools_used

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
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
                    # Ene: never leak errors to public chat
                    # Only send a vague message to Dad in DMs
                    caller_id = f"{msg.channel}:{msg.sender_id}"
                    if caller_id in DAD_IDS:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"something broke: {str(e)[:200]}"
                        ))
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
        """Clean LLM output before sending to Discord. Ene-specific."""
        if not content:
            return None

        # Strip reflection blocks (## Reflection, ## Internal, etc.)
        content = re.sub(r'##\s*(?:Reflection|Internal|Thinking|Analysis).*?(?=\n##|\Z)', '', content, flags=re.DOTALL)

        # Strip leaked system paths
        content = re.sub(r'C:\\Users\\[^\s]+', '[redacted]', content)
        content = re.sub(r'/home/[^\s]+', '[redacted]', content)

        # Strip leaked IDs
        content = re.sub(r'discord:\d{10,}', '[redacted]', content)
        content = re.sub(r'telegram:\d{5,}', '[redacted]', content)

        # Strip any stack traces that leaked through
        content = re.sub(r'Traceback \(most recent call last\).*?(?=\n\n|\Z)', '', content, flags=re.DOTALL)
        content = re.sub(r'(?:litellm\.|openai\.|httpx\.)[\w.]+Error.*', '', content)

        # Strip markdown bold in public channels
        is_public = msg.channel == "discord" and msg.metadata.get("guild_id")
        if is_public:
            content = content.replace("**", "")

        content = content.strip()
        if not content:
            return None

        # Length limits
        if is_public and len(content) > 500:
            # Truncate at sentence boundary for public channels
            sentences = re.split(r'(?<=[.!?])\s+', content)
            truncated = ""
            for s in sentences:
                if len(truncated) + len(s) > 450:
                    break
                truncated += s + " "
            content = truncated.strip()
            if not content:
                content = sentences[0][:450] if sentences else ""
            logger.debug(f"Truncated public response from {len(content)} chars")

        # Hard Discord limit
        if len(content) > 1900:
            content = content[:1900] + "..."

        return content

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

        # Respond if "ene" is mentioned in the message
        if "ene" in msg.content.lower():
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

    async def _process_message(self, msg: InboundMessage, session_key: str | None = None) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
            session_key: Override session key (used by process_direct).
        
        Returns:
            The response message, or None if no response needed.
        """
        # System messages route back via chat_id ("channel:chat_id")
        if msg.channel == "system":
            return await self._process_system_message(msg)

        # Ene: track who's talking for tool permission checks
        self._current_caller_id = f"{msg.channel}:{msg.sender_id}"
        self._last_message_time = _time.time()  # Ene: update for idle tracking

        # Ene: tell modules who is speaking (for person cards, trust context)
        self.module_registry.set_current_sender(
            msg.sender_id, msg.channel, msg.metadata or {}
        )

        # Ene: DM access gate — block untrusted DMs before LLM call (zero cost)
        if self._is_dm(msg) and not self._dm_access_allowed(msg):
            logger.info(f"DM rejected from {msg.channel}:{msg.sender_id} (trust too low)")
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Sorry, I can't chat in DMs right now! Come say hi in the server first \U0001f499",
                reply_to=msg.metadata.get("message_id") if msg.metadata else None,
                metadata=msg.metadata or {},
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Ene: lurk mode — store message but don't respond
        if not self._should_respond(msg):
            author = msg.metadata.get('author_name', msg.sender_id)
            session.add_message("user", f"{author}: {msg.content}")
            self.sessions.save(session)
            # Write to interaction log
            self.memory.append_interaction_log(
                session_key=key, role="user", content=msg.content, author_name=author,
            )
            logger.debug(f"Lurking on message from {msg.sender_id} in {key}")
            # Ene: notify modules even for lurked messages
            asyncio.create_task(self.module_registry.notify_message(msg, responded=False))
            return None
        
        # Handle slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            # Capture messages before clearing (avoid race condition with background task)
            messages_to_archive = session.messages.copy()
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)

            async def _consolidate_and_cleanup():
                temp_session = Session(key=session.key)
                temp_session.messages = messages_to_archive
                await self._consolidate_memory(temp_session, archive_all=True)

            asyncio.create_task(_consolidate_and_cleanup())
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started. Memory consolidation in progress.")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 nanobot commands:\n/new — Start a new conversation\n/help — Show available commands")
        
        if len(session.messages) > self.memory_window:
            asyncio.create_task(self._consolidate_memory(session))

        self._set_tool_context(msg.channel, msg.chat_id)
        initial_messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        final_content, tools_used = await self._run_agent_loop(initial_messages)

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # Ene: store raw response in session before cleaning
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content,
                            tools_used=tools_used if tools_used else None)
        self.sessions.save(session)

        # Ene: write to interaction logs (hardcoded, not LLM-driven)
        self.memory.append_interaction_log(
            session_key=key, role="user", content=msg.content,
            author_name=msg.metadata.get("author_name"),
        )
        self.memory.append_interaction_log(
            session_key=key, role="assistant", content=final_content,
            tools_used=tools_used if tools_used else None,
        )

        # Ene: notify modules that a message was processed
        asyncio.create_task(self.module_registry.notify_message(msg, responded=True))

        # Ene: clean response (strip leaks, enforce length, etc.)
        cleaned = self._ene_clean_response(final_content, msg)
        if not cleaned:
            logger.debug("Response cleaned to empty, not sending")
            return None

        preview = cleaned[:120] + "..." if len(cleaned) > 120 else cleaned
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=cleaned,
            reply_to=msg.metadata.get("message_id"),  # Ene: thread replies to original message
            metadata=msg.metadata or {},
        )
    
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

        New memory architecture:
        - Interaction logs: written in real-time by _process_message (not here)
        - Diary entries: summarized here from the conversation buffer
        - Core memory: written by Ene via save_memory tool (not here)

        Args:
            archive_all: If True, summarize all messages (for /new command).
        """
        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info(f"Diary consolidation (archive_all): {len(session.messages)} messages")
        else:
            keep_count = self.memory_window // 2
            if len(session.messages) <= keep_count:
                return

            messages_to_process = len(session.messages) - session.last_consolidated
            if messages_to_process <= 0:
                return

            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return
            logger.info(f"Diary consolidation: {len(old_messages)} messages to summarize")

        # Build conversation text
        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}")
        conversation = "\n".join(lines)

        if not conversation.strip():
            if not archive_all:
                session.last_consolidated = len(session.messages) - keep_count
            return

        prompt = f"""You are writing a diary entry for Ene (a digital AI companion).
Summarize this conversation in FIRST PERSON, as Ene would write it.
Keep it brief (2-5 sentences). Focus on: what happened, who was involved,
anything emotionally or factually important, any decisions made.

Write naturally, like a personal journal entry. No JSON, no markdown headers.

## Conversation
{conversation}

Write the diary entry now:"""

        # Use configurable consolidation model (falls back to main model)
        model = self.consolidation_model or self.model
        max_retries = 2

        for attempt in range(max_retries + 1):
            try:
                response = await self.provider.chat(
                    messages=[
                        {"role": "system", "content": "Write brief first-person diary entries. No JSON, no markdown. Just natural journaling."},
                        {"role": "user", "content": prompt},
                    ],
                    model=model,
                )
                text = (response.content or "").strip()
                if not text:
                    logger.warning("Diary consolidation: empty response, skipping")
                    break

                # Strip any markdown fences the model might add
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

                self.memory.append_diary(text)
                logger.info(f"Diary entry written: {text[:80]}")

                if archive_all:
                    session.last_consolidated = 0
                else:
                    session.last_consolidated = len(session.messages) - keep_count
                return  # success

            except Exception as e:
                if attempt < max_retries:
                    # Drop oldest messages and retry
                    old_messages = old_messages[10:]
                    if not old_messages:
                        logger.warning("Diary consolidation: no messages left after truncation")
                        break
                    lines = [
                        f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}: {m['content']}"
                        for m in old_messages if m.get("content")
                    ]
                    conversation = "\n".join(lines)
                    prompt = f"""Write a brief first-person diary entry for Ene summarizing this conversation (2-5 sentences):

{conversation}"""
                    logger.warning(f"Diary consolidation attempt {attempt+1} failed ({e}), retrying")
                else:
                    logger.error(f"Diary consolidation failed after {max_retries+1} attempts: {e}")
                    if not archive_all:
                        session.last_consolidated = len(session.messages) - keep_count

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
