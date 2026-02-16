"""Tests for context window improvements.

Covers:
- Reflection stripping (comprehensive regex)
- Hybrid history (summary + recent verbatim)
- Token estimation
- Session responded count
- Identity re-anchoring trigger
"""

import pytest
import re

from nanobot.session.manager import Session


# ============================================================
# Reflection Stripping Tests
# ============================================================

# Import the regex patterns from loop.py by reconstructing them here
# (since _ene_clean_response is a method on AgentLoop, we test the patterns directly)

def _strip_reflections(content: str) -> str:
    """Apply the same reflection stripping as _ene_clean_response."""
    # Pattern 1: Markdown heading reflection blocks
    content = re.sub(
        r'#{2,4}\s*(?:\*\*)?(?:[\w\s]*?)'
        r'(?:Reflection|Internal|Thinking|Analysis|Self[- ]?Assessment|Observations?|Notes? to Self)'
        r'(?:[\w\s]*?)(?:\*\*)?\s*\n.*?(?=\n#{2,4}\s|\Z)',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )
    # Pattern 2: Bold-only reflection headers
    content = re.sub(
        r'\*\*(?:[\w\s]*?)'
        r'(?:Reflection|Internal|Thinking|Analysis|Self[- ]?Assessment|Observations?|Notes? to Self)'
        r'(?:[\w\s]*?)\*\*\s*\n.*?(?=\n\*\*|\n#{2,4}\s|\Z)',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )
    # Pattern 3: Inline reflection paragraphs
    content = re.sub(
        r'\n(?:Let me (?:reflect|think|analyze)|Thinking (?:about|through)|Upon reflection|'
        r'Internal (?:note|thought)|Note to self|My (?:reflection|analysis|thoughts?))[\s:,].*?(?=\n\n|\Z)',
        '', content, flags=re.DOTALL | re.IGNORECASE
    )
    return content.strip()


class TestReflectionStripping:
    """Test that reflection blocks are properly stripped from responses."""

    def test_basic_h2_reflection(self):
        content = "Hello world!\n\n## Reflection\nThis is my internal thinking.\nMore thinking here.\n"
        result = _strip_reflections(content)
        assert "Hello world!" in result
        assert "internal thinking" not in result

    def test_h3_reflection(self):
        content = "Response text.\n\n### Reflection\nThinking deeply.\n"
        result = _strip_reflections(content)
        assert "Response text." in result
        assert "Thinking deeply" not in result

    def test_h4_reflection(self):
        content = "Response.\n\n#### Internal Thoughts\nShould not appear.\n"
        result = _strip_reflections(content)
        assert "Response." in result
        assert "Should not appear" not in result

    def test_case_insensitive(self):
        content = "OK.\n\n## REFLECTION\nUPPERCASE.\n"
        result = _strip_reflections(content)
        assert "OK." in result
        assert "UPPERCASE" not in result

    def test_lowercase_reflection(self):
        content = "Fine.\n\n## reflection\nlowercase thinking.\n"
        result = _strip_reflections(content)
        assert "Fine." in result
        assert "lowercase thinking" not in result

    def test_mixed_case(self):
        content = "Yep.\n\n## My Reflection\nMixed case block.\n"
        result = _strip_reflections(content)
        assert "Yep." in result
        assert "Mixed case block" not in result

    def test_internal_thoughts(self):
        content = "Sure.\n\n## Internal Thoughts\nSecret stuff.\n"
        result = _strip_reflections(content)
        assert "Sure." in result
        assert "Secret stuff" not in result

    def test_self_assessment(self):
        content = "Done.\n\n## Self-Assessment\nHow I did.\n"
        result = _strip_reflections(content)
        assert "Done." in result
        assert "How I did" not in result

    def test_self_assessment_no_hyphen(self):
        content = "Done.\n\n## Self Assessment\nEvaluation.\n"
        result = _strip_reflections(content)
        assert "Done." in result
        assert "Evaluation" not in result

    def test_observations_header(self):
        content = "Hello.\n\n## Observations\nI noticed things.\n"
        result = _strip_reflections(content)
        assert "Hello." in result
        assert "noticed things" not in result

    def test_note_to_self(self):
        content = "Hi.\n\n## Note to Self\nRemember this.\n"
        result = _strip_reflections(content)
        assert "Hi." in result
        assert "Remember this" not in result

    def test_bold_reflection_header(self):
        content = "Howdy.\n\n**Reflection**\nBold header thinking.\n"
        result = _strip_reflections(content)
        assert "Howdy." in result
        assert "Bold header thinking" not in result

    def test_bold_internal_thoughts(self):
        content = "OK.\n\n**Internal Thoughts**\nHidden reasoning.\n"
        result = _strip_reflections(content)
        assert "OK." in result
        assert "Hidden reasoning" not in result

    def test_inline_let_me_reflect(self):
        content = "Main answer.\n\nLet me reflect on this conversation for a moment.\nThis went well."
        result = _strip_reflections(content)
        assert "Main answer." in result
        assert "Let me reflect" not in result

    def test_inline_thinking_about(self):
        content = "Response.\n\nThinking about this more carefully, I should reconsider."
        result = _strip_reflections(content)
        assert "Response." in result
        assert "Thinking about" not in result

    def test_inline_upon_reflection(self):
        content = "Answer.\n\nUpon reflection, my analysis was correct."
        result = _strip_reflections(content)
        assert "Answer." in result
        assert "Upon reflection" not in result

    def test_inline_note_to_self(self):
        content = "Reply.\n\nNote to self: remember this person likes cats."
        result = _strip_reflections(content)
        assert "Reply." in result
        assert "remember this person" not in result

    def test_reflection_between_sections(self):
        """Reflection block between two valid sections should only strip the reflection."""
        content = "## Response\nHere is my answer.\n\n## Reflection\nThinking...\n\n## Summary\nFinal notes."
        result = _strip_reflections(content)
        assert "Here is my answer." in result
        assert "Thinking..." not in result
        assert "Final notes." in result

    def test_no_reflection_untouched(self):
        """Content without reflection should pass through unchanged."""
        content = "Just a normal response with no internal thoughts at all."
        result = _strip_reflections(content)
        assert result == content

    def test_analysis_header(self):
        content = "Done.\n\n## Analysis\nDeep analysis here.\n"
        result = _strip_reflections(content)
        assert "Done." in result
        assert "Deep analysis" not in result

    def test_thinking_header(self):
        content = "Yes.\n\n### Thinking\nThought process.\n"
        result = _strip_reflections(content)
        assert "Yes." in result
        assert "Thought process" not in result

    def test_multiline_reflection(self):
        """Reflection with multiple paragraphs should all be stripped."""
        content = "Answer.\n\n## Reflection\nFirst paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        result = _strip_reflections(content)
        assert "Answer." in result
        assert "First paragraph" not in result
        assert "Second paragraph" not in result
        assert "Third paragraph" not in result


# ============================================================
# Hybrid History Tests
# ============================================================

class TestHybridHistory:
    """Test the hybrid history method on Session."""

    def _make_session(self, n_messages: int) -> Session:
        """Create a session with N user/assistant message pairs."""
        session = Session(key="test:hybrid")
        for i in range(n_messages):
            session.add_message("user", f"User message {i}")
            session.add_message("assistant", f"Assistant response {i}")
        return session

    def test_small_session_no_summary(self):
        """Sessions with few messages should return all messages, no summary."""
        session = self._make_session(5)  # 10 messages total
        history = session.get_hybrid_history(recent_count=20)
        assert len(history) == 10  # All messages
        assert all(m["role"] in ("user", "assistant") for m in history)

    def test_large_session_without_summary(self):
        """Large session without summary just returns recent messages."""
        session = self._make_session(30)  # 60 messages total
        history = session.get_hybrid_history(recent_count=20)
        assert len(history) == 20  # Only recent
        # Last message should be the most recent
        assert history[-1]["content"] == "Assistant response 29"

    def test_large_session_with_summary(self):
        """Large session with summary includes summary + recent."""
        session = self._make_session(30)
        summary = "We discussed cats and coding."
        history = session.get_hybrid_history(recent_count=20, summary=summary)
        # 2 (summary pair) + 20 (recent) = 22
        assert len(history) == 22
        # First message should be the summary
        assert "[Earlier conversation summary]" in history[0]["content"]
        assert "cats and coding" in history[0]["content"]
        # Second should be ack
        assert history[1]["role"] == "assistant"
        assert "remember" in history[1]["content"].lower()

    def test_summary_position_before_recent(self):
        """Summary should come BEFORE recent messages (Lost in the Middle pattern)."""
        session = self._make_session(30)
        history = session.get_hybrid_history(
            recent_count=20,
            summary="Old context here."
        )
        # Find summary index
        summary_idx = None
        for i, m in enumerate(history):
            if "Earlier conversation summary" in m.get("content", ""):
                summary_idx = i
                break
        assert summary_idx is not None
        assert summary_idx == 0  # Summary is first

    def test_recent_count_respected(self):
        """Should return exactly recent_count messages from the end."""
        session = self._make_session(50)  # 100 messages
        history = session.get_hybrid_history(recent_count=10)
        assert len(history) == 10
        # Verify they're the last 10
        assert history[-1]["content"] == "Assistant response 49"
        assert history[0]["content"] == "User message 45"

    def test_empty_session(self):
        """Empty session should return empty list."""
        session = Session(key="test:empty")
        history = session.get_hybrid_history(recent_count=20)
        assert history == []

    def test_summary_with_empty_recent(self):
        """Summary with no messages should return just summary pair."""
        session = Session(key="test:empty")
        history = session.get_hybrid_history(recent_count=20, summary="Old stuff.")
        assert len(history) == 2
        assert "Old stuff" in history[0]["content"]


# ============================================================
# Token Estimation Tests
# ============================================================

class TestTokenEstimation:
    """Test the token estimation on Session."""

    def test_empty_session(self):
        session = Session(key="test:tokens")
        assert session.estimate_tokens() == 0

    def test_short_messages(self):
        session = Session(key="test:tokens")
        session.add_message("user", "Hello")  # 5 chars = ~1 token
        session.add_message("assistant", "Hi there")  # 8 chars = ~2 tokens
        tokens = session.estimate_tokens()
        # 13 chars // 4 = 3
        assert tokens == 3

    def test_longer_messages(self):
        session = Session(key="test:tokens")
        # 400 chars = ~100 tokens
        session.add_message("user", "a" * 400)
        assert session.estimate_tokens() == 100

    def test_multiple_messages(self):
        session = Session(key="test:tokens")
        for _ in range(10):
            session.add_message("user", "a" * 40)  # 40 chars each
        # 400 total chars // 4 = 100 tokens
        assert session.estimate_tokens() == 100

    def test_empty_content(self):
        session = Session(key="test:tokens")
        session.messages.append({"role": "user"})  # No content key
        assert session.estimate_tokens() == 0


# ============================================================
# Responded Count Tests
# ============================================================

class TestRespondedCount:
    """Test the responded count on Session."""

    def test_empty_session(self):
        session = Session(key="test:count")
        assert session.get_responded_count() == 0

    def test_only_user_messages(self):
        """Lurked messages (user only) should not count."""
        session = Session(key="test:count")
        for i in range(20):
            session.add_message("user", f"Lurked message {i}")
        assert session.get_responded_count() == 0

    def test_mixed_messages(self):
        session = Session(key="test:count")
        # 5 lurked + 3 responded
        for i in range(5):
            session.add_message("user", f"Lurked {i}")
        for i in range(3):
            session.add_message("user", f"Asked {i}")
            session.add_message("assistant", f"Replied {i}")
        assert session.get_responded_count() == 3

    def test_all_responded(self):
        session = Session(key="test:count")
        for i in range(10):
            session.add_message("user", f"Q{i}")
            session.add_message("assistant", f"A{i}")
        assert session.get_responded_count() == 10


# ============================================================
# Re-anchoring Trigger Tests
# ============================================================

class TestReanchoring:
    """Test re-anchoring trigger logic."""

    def test_no_reanchor_empty_session(self):
        """Empty session should not trigger re-anchoring."""
        session = Session(key="test:reanchor")
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        # Simulate the check from _should_reanchor (interval = 6)
        should = assistant_count > 0 and assistant_count % 6 == 0
        assert should is False

    def test_reanchor_at_6(self):
        """Should trigger at exactly 6 assistant messages (interval)."""
        session = Session(key="test:reanchor")
        for i in range(6):
            session.add_message("user", f"Q{i}")
            session.add_message("assistant", f"A{i}")
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        should = assistant_count > 0 and assistant_count % 6 == 0
        assert should is True

    def test_no_reanchor_at_7(self):
        session = Session(key="test:reanchor")
        for i in range(7):
            session.add_message("user", f"Q{i}")
            session.add_message("assistant", f"A{i}")
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        should = assistant_count > 0 and assistant_count % 6 == 0
        assert should is False

    def test_reanchor_at_12(self):
        """Should trigger again at 12 (2x interval)."""
        session = Session(key="test:reanchor")
        for i in range(12):
            session.add_message("user", f"Q{i}")
            session.add_message("assistant", f"A{i}")
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        should = assistant_count > 0 and assistant_count % 6 == 0
        assert should is True

    def test_lurked_messages_dont_affect_reanchor(self):
        """Only assistant messages count, not lurked user messages."""
        session = Session(key="test:reanchor")
        # 50 lurked + 6 responded
        for i in range(50):
            session.add_message("user", f"Lurked {i}")
        for i in range(6):
            session.add_message("user", f"Q{i}")
            session.add_message("assistant", f"A{i}")
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        should = assistant_count > 0 and assistant_count % 6 == 0
        assert should is True
