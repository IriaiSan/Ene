"""Ene response cleaning: sanitize LLM output before sending to users.

Single chokepoint for all output sanitization (WHITELIST X2, X3).
All functions are pure str → str|None with no instance state.
"""

import re
from typing import Any

from loguru import logger


def condense_for_session(content: str, metadata: dict) -> str:
    """Condense thread-formatted content for session storage.

    The conversation tracker formats ALL active threads each time. If we store
    the full thread context in session history, the LLM sees the same thread
    messages duplicated across turns. This strips the thread chrome and keeps
    only the #msgN lines from the current batch.

    Runs on any thread-formatted content (thread_count > 0) OR debounced
    batches — not just debounced, since single-message turns can also have
    thread context injected by the formatter.
    """
    is_threaded = (
        metadata.get("debounced")
        or metadata.get("thread_count", 0) > 0
        or metadata.get("bg_thread_count", 0) > 0
    )
    if not is_threaded:
        return content

    lines = content.split("\n")
    msg_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#msg"):
            # Strip the #msgN tag — tags reset each batch, causing collisions
            # between session history and current thread context
            stripped = re.sub(r'^#msg\d+\s+', '', stripped)
            if stripped:
                msg_lines.append(stripped)
        elif stripped.startswith("[background"):
            msg_lines.append("[background]")

    if msg_lines:
        return "\n".join(msg_lines)

    return content


def clean_response(content: str, msg: Any, is_public: bool = False) -> str | None:
    """Clean LLM output before sending to Discord/Telegram.

    Args:
        content: Raw LLM output text.
        msg: InboundMessage (used for metadata like guild_id).
        is_public: Whether this is a public channel message.

    Returns:
        Cleaned string or None if nothing remains.
    """
    if not content:
        return None

    # --- Strip reflection blocks ---
    content = re.sub(
        r'#{2,4}\s*(?:\*\*)?(?:[\w\s]*?)'
        r'(?:Reflection|Internal|Thinking|Analysis|Self[- ]?Assessment|Observations?|Notes? to Self)'
        r'(?:[\w\s]*?)(?:\*\*)?\s*\n.*?(?=\n#{2,4}\s|\Z)',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )
    content = re.sub(
        r'\*\*(?:[\w\s]*?)'
        r'(?:Reflection|Internal|Thinking|Analysis|Self[- ]?Assessment|Observations?|Notes? to Self)'
        r'(?:[\w\s]*?)\*\*\s*\n.*?(?=\n\*\*|\n#{2,4}\s|\Z)',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )
    content = re.sub(
        r'\n(?:Let me (?:reflect|think|analyze)|Thinking (?:about|through)|Upon reflection|'
        r'Internal (?:note|thought)|Note to self|My (?:reflection|analysis|thoughts?))[\s:,].*?(?=\n\n|\Z)',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )

    # --- Strip DeepSeek model refusal patterns ---
    if '作为一个人工智能' in content or '我还没学习' in content:
        content = "Nah, not touching that one."
    content = re.sub(
        r'(?:As an AI (?:language model|assistant)|I\'m (?:designed|programmed) to be (?:helpful|harmless)).*?[.!]',
        '', content, flags=re.IGNORECASE
    )

    # --- Language enforcement: English only ---
    _lang_sample = re.sub(r'[\U0001F000-\U0001FFFF\u2600-\u27BF\u2300-\u23FF\u200d\ufe0f]', '', content[:200])
    _is_non_english = False
    if len(_lang_sample) > 20:
        _non_ascii = sum(1 for c in _lang_sample if ord(c) > 127)
        if _non_ascii / len(_lang_sample) > 0.3:
            _is_non_english = True
    if _is_non_english:
        logger.warning("Language enforcement: non-English response blocked")
        content = "English only for me — I don't do other languages."

    # --- Strip leaked tool call XML (WHITELIST X3) ---
    content = re.sub(r'<function_calls>.*?</function_calls>', '', content, flags=re.DOTALL)
    content = re.sub(r'<function_calls>.*', '', content, flags=re.DOTALL)
    content = re.sub(r'</?(?:invoke|parameter|antml:invoke|antml:parameter)[^>]*>', '', content)

    # --- Strip LLM error messages that leaked through ---
    content = re.sub(r'Error calling LLM:.*', '', content, flags=re.DOTALL)
    content = re.sub(r'(?:APIError|RateLimitError|AuthenticationError):.*', '', content)

    # --- Strip leaked system paths ---
    content = re.sub(r'C:\\Users\\[^\s]+', '[redacted]', content)
    content = re.sub(r'/home/[^\s]+', '[redacted]', content)

    # --- Strip leaked IDs ---
    content = re.sub(r'discord:\d{10,}', '[redacted]', content)
    content = re.sub(r'telegram:\d{5,}', '[redacted]', content)

    # --- Strip stack traces ---
    content = re.sub(r'Traceback \(most recent call last\).*?(?=\n\n|\Z)', '', content, flags=re.DOTALL)
    content = re.sub(r'(?:litellm\.|openai\.|httpx\.)[\w.]+Error.*', '', content)

    # --- Strip assistant-tone endings ---
    content = re.sub(
        r'\s*(?:Let me know if (?:you )?(?:need|want|have).*?[.!]|'
        r'(?:Is there )?[Aa]nything else.*?[.!?]|'
        r'How can I (?:help|assist).*?[.!?]|'
        r'(?:Feel free to|Don\'t hesitate to).*?[.!]|'
        r'I\'m here (?:to help|if you need).*?[.!]|'
        r'Hope (?:this|that) helps.*?[.!]|'
        r'Happy to help.*?[.!])\s*$',
        '', content, flags=re.IGNORECASE
    )

    # --- Strip "I see..." openers ---
    content = re.sub(
        r'^(?:I (?:see|notice|observe|can see) (?:that )?)',
        '', content, flags=re.IGNORECASE
    )

    # --- Strip internal planning blocks ---
    content = re.sub(
        r'\n\n?(?:Next steps|Action items|My plan|What I (?:should|need to) do|I should (?:also )?(?:be|keep|watch|maintain|monitor|continue))[\s:.].*',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )
    content = re.sub(
        r'\n\n?(?:The (?:key|goal|plan|idea|priority|focus) is to\b).*',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )

    # --- Strip markdown bold in public channels ---
    if is_public:
        content = content.replace("**", "")

    content = content.strip()
    if not content:
        return None

    # --- Length limits ---
    if is_public and len(content) > 500:
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
