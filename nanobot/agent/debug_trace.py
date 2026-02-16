"""Debug trace logger — writes detailed per-message markdown logs.

Logs everything: inbound message, system prompt, full messages array,
LLM response, tool calls, tool results, cleaning steps, final output.

Output: workspace/logs/debug/YYYY-MM-DD_HHMMSS_{sender}.md
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class DebugTrace:
    """Captures a single message's full processing trace."""

    def __init__(self, log_dir: Path, sender: str, channel: str):
        self._enabled = True
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safe_sender = sender.replace(":", "_")[:20]
        self._log_dir = log_dir / "debug"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._log_dir / f"{ts}_{safe_sender}.md"
        self._lines: list[str] = []
        self._start = time.monotonic()

        self._write(f"# Debug Trace: {channel}:{sender}")
        self._write(f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    def _write(self, text: str) -> None:
        self._lines.append(text)

    def _elapsed(self) -> str:
        return f"{time.monotonic() - self._start:.2f}s"

    def log_inbound(self, msg: Any) -> None:
        """Log the raw inbound message."""
        self._write(f"## 1. Inbound Message ({self._elapsed()})")
        self._write(f"- **Channel:** {msg.channel}")
        self._write(f"- **Sender:** {msg.sender_id}")
        self._write(f"- **Chat ID:** {msg.chat_id}")
        self._write(f"- **Session key:** {msg.session_key}")
        if msg.metadata:
            self._write(f"- **Metadata:** `{json.dumps(msg.metadata, default=str)}`")
        self._write(f"\n**Content:**\n```\n{msg.content}\n```\n")

    def log_should_respond(self, should: bool, reason: str = "") -> None:
        """Log the should_respond decision."""
        self._write(f"## 2. Response Decision ({self._elapsed()})")
        self._write(f"- **Should respond:** {should}")
        if reason:
            self._write(f"- **Reason:** {reason}")
        self._write("")

    def log_system_prompt(self, prompt: str) -> None:
        """Log the full system prompt sent to the LLM."""
        self._write(f"## 3. System Prompt ({self._elapsed()})")
        self._write(f"**Length:** {len(prompt)} chars (~{len(prompt) // 4} tokens)\n")
        self._write(f"<details><summary>Full system prompt</summary>\n\n```\n{prompt}\n```\n</details>\n")

    def log_messages_array(self, messages: list[dict]) -> None:
        """Log the full messages array sent to the LLM."""
        self._write(f"## 4. Messages Array ({self._elapsed()})")
        self._write(f"**Count:** {len(messages)} messages\n")
        for i, m in enumerate(messages):
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, str):
                preview = content[:300] + "..." if len(content) > 300 else content
            else:
                preview = str(content)[:300]
            self._write(f"### [{i}] {role}")
            self._write(f"```\n{preview}\n```\n")

    def log_llm_call(self, iteration: int, model: str) -> None:
        """Log that an LLM call is being made."""
        self._write(f"## 5. LLM Call — Iteration {iteration} ({self._elapsed()})")
        self._write(f"- **Model:** {model}\n")

    def log_llm_response(self, response: Any) -> None:
        """Log the LLM response."""
        self._write(f"### LLM Response ({self._elapsed()})")
        if response.reasoning_content:
            rc = response.reasoning_content
            preview = rc[:500] + "..." if len(rc) > 500 else rc
            self._write(f"**Reasoning:**\n```\n{preview}\n```\n")
        if response.content:
            self._write(f"**Content:**\n```\n{response.content}\n```\n")
        if response.has_tool_calls:
            self._write(f"**Tool calls:** {len(response.tool_calls)}")
            for tc in response.tool_calls:
                args = json.dumps(tc.arguments, ensure_ascii=False, indent=2)
                self._write(f"- `{tc.name}`: ```{args}```")
        self._write("")

    def log_tool_result(self, name: str, result: str) -> None:
        """Log a tool execution result."""
        preview = result[:500] + "..." if len(result) > 500 else result
        self._write(f"### Tool Result: {name} ({self._elapsed()})")
        self._write(f"```\n{preview}\n```\n")

    def log_cleaning(self, raw: str, cleaned: str | None) -> None:
        """Log the response cleaning step."""
        self._write(f"## 6. Response Cleaning ({self._elapsed()})")
        self._write(f"**Raw length:** {len(raw)} chars")
        self._write(f"**Raw:**\n```\n{raw}\n```\n")
        if cleaned:
            self._write(f"**Cleaned length:** {len(cleaned)} chars")
            if cleaned != raw:
                self._write(f"**Cleaned:**\n```\n{cleaned}\n```\n")
            else:
                self._write("*(no changes)*\n")
        else:
            self._write("**Cleaned to empty — no response sent.**\n")

    def log_final(self, sent: str | None) -> None:
        """Log the final output."""
        self._write(f"## 7. Final Output ({self._elapsed()})")
        if sent:
            self._write(f"```\n{sent}\n```\n")
        else:
            self._write("*(nothing sent)*\n")
        self._write(f"---\n**Total time:** {self._elapsed()}")

    def save(self) -> Path:
        """Write the trace to disk and return the path."""
        self._path.write_text("\n".join(self._lines), encoding="utf-8")
        return self._path
