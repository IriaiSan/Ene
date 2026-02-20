"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any, TYPE_CHECKING

from nanobot.agent.memory import MemoryStore
from nanobot.agent.prompts.loader import PromptLoader
from nanobot.agent.skills import SkillsLoader

if TYPE_CHECKING:
    from nanobot.ene import ModuleRegistry


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.

    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]

    def __init__(self, workspace: Path, module_registry: "ModuleRegistry | None" = None):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self._module_registry = module_registry
        self._prompts = PromptLoader()
        self._muted_users: dict[str, float] = {}  # Ene: mute state for context injection

    def set_mute_state(self, muted_users: dict[str, float]) -> None:
        """Update mute state so Ene can see who she's muted."""
        self._muted_users = muted_users

    def _get_mute_context(self) -> str:
        """Build mute awareness context for Ene's system prompt."""
        import time
        now = time.time()
        active = {k: v for k, v in self._muted_users.items() if v > now}
        if not active:
            return ""
        lines = ["Currently muted:"]
        for caller_id, expires in active.items():
            remaining = max(1, int((expires - now) / 60) + 1)
            # Try to resolve display name via social module
            display = caller_id
            if self._module_registry:
                social = self._module_registry.get_module("social")
                if social and hasattr(social, "registry") and social.registry:
                    person = social.registry.get_by_platform_id(caller_id)
                    if person:
                        display = person.display_name
            lines.append(f"- {display} ({remaining} min left)")
        return "\n".join(lines)
    
    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.
        
        Args:
            skill_names: Optional list of skills to include.
        
        Returns:
            Complete system prompt.
        """
        parts = []
        
        # Core identity
        parts.append(self._get_identity())
        
        # Bootstrap files
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)
        
        # Ene module context blocks (core memory, diary, etc.)
        if self._module_registry:
            module_context = self._module_registry.get_all_context_blocks()
            if module_context:
                parts.append(f"# Memory\n\n{module_context}")
        else:
            # Fallback: legacy memory context (no modules registered)
            memory = self.memory.get_memory_context()
            if memory:
                parts.append(f"# Memory\n\n{memory}")
        
        # Skills - progressive loading
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")
        
        # 2. Available skills: only show summary (agent uses read_file to load)
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")
        
        return "\n\n---\n\n".join(parts)
    
    def _is_dad_caller(self) -> bool:
        """Check if the current caller is Dad via the module registry."""
        if not self._module_registry:
            return False
        platform_id = self._module_registry.get_current_platform_id()
        if not platform_id:
            return False
        # Import here to avoid circular imports
        from nanobot.ene.social.person import DAD_IDS
        return platform_id in DAD_IDS

    def _get_identity(self) -> str:
        """Get the core identity section.

        Dad sees the full technical identity (workspace paths, all tools,
        system details). Everyone else sees a stripped version — Ene knows
        who she is but doesn't leak implementation details.
        """
        from datetime import datetime
        import time as _time
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"

        if self._is_dad_caller():
            return self._get_identity_full(now, tz)
        return self._get_identity_public(now, tz)

    def _get_identity_full(self, now: str, tz: str) -> str:
        """Full identity block for Dad — all technical details."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return self._prompts.load(
            "identity_dad",
            now=now,
            tz=tz,
            runtime=runtime,
            workspace_path=workspace_path,
        )

    def _get_identity_public(self, now: str, tz: str) -> str:
        """Stripped identity block for non-Dad callers.

        No workspace paths, no file names, no technical architecture.
        Ene knows she has memory and can talk about herself naturally,
        but doesn't reveal implementation details.
        """
        return self._prompts.load(
            "identity_public",
            now=now,
            tz=tz,
            mute_context=self._get_mute_context(),
        )
    
    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []
        
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")
        
        return "\n\n".join(parts) if parts else ""
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        reanchor: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
            reanchor: Optional identity re-anchoring text to inject near
                the end of history (high-attention zone) to fight persona drift.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        system_prompt = self.build_system_prompt(skill_names)

        # Ene: inject dynamic per-message context (retrieved memories, entities)
        if self._module_registry and current_message:
            dynamic_context = self._module_registry.get_all_dynamic_context(current_message)
            if dynamic_context:
                system_prompt += f"\n\n{dynamic_context}"

        if channel and chat_id:
            system_prompt += f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
        messages.append({"role": "system", "content": system_prompt})

        # History
        messages.extend(history)

        # Ene: identity re-anchoring — inject near end of history (high-attention zone)
        # Research: DeepSeek v3.2 drifts after 8-12 turns. Periodic injection
        # of a brief personality reminder keeps Ene from going generic.
        if reanchor:
            messages.append({"role": "system", "content": reanchor})

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text
        
        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        
        if not images:
            return text
        return images + [{"type": "text", "text": text}]
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
        
        Returns:
            Updated message list.
        """
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.
        
        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).
        
        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        # Thinking models reject history without this
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        
        messages.append(msg)
        return messages
