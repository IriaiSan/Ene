"""Stress testing — scripted message generators and state verifiers.

Generates realistic message sequences for load testing, trust progression,
and edge case scenarios. StateVerifier follows the tau-bench pattern:
verify actual state (memory entries, trust scores, thread states) not
just text output.

Usage:
    script = StressTest.generate_multi_user(user_count=10, messages_per_user=10)
    results = await lab.run_script(script)
    assert StateVerifier.verify_no_duplicate_responses(results)
"""

from __future__ import annotations

import random
import string
import uuid
from typing import Any


class StressTest:
    """Scripted message generators for load and behavior testing."""

    @staticmethod
    def generate_multi_user(
        user_count: int = 10,
        messages_per_user: int = 10,
        ene_mention_rate: float = 0.3,
        chat_id: str = "lab_general",
        guild_id: str = "lab_guild",
    ) -> list[dict[str, Any]]:
        """Generate interleaved messages from N users.

        Messages are shuffled to simulate realistic concurrent conversation.
        Some messages mention Ene (should trigger RESPOND), others don't
        (should be classified as CONTEXT or DROP).

        Args:
            user_count: Number of distinct users.
            messages_per_user: Messages per user.
            ene_mention_rate: Probability a message mentions Ene.
            chat_id: Chat/channel ID.
            guild_id: Guild/server ID.

        Returns:
            Shuffled list of message dicts.
        """
        users = [
            {
                "sender_id": f"user_{i:03d}",
                "display_name": f"User{i}",
                "username": f"user{i}",
            }
            for i in range(user_count)
        ]

        # Conversation topics for variety
        topics = [
            "what do you think about the weather today?",
            "anyone played the new game?",
            "I just finished a book about AI",
            "does anyone know how to cook pasta?",
            "the meeting was so boring lol",
            "check out this meme I found",
            "I'm thinking about getting a new keyboard",
            "who's going to the event this weekend?",
            "that movie was actually pretty good",
            "I need help with my code",
        ]

        ene_messages = [
            "ene what do you think?",
            "hey ene, tell me something fun",
            "ene do you remember what we talked about?",
            "@ene help me with this",
            "ene are you there?",
            "what's your opinion on this ene?",
            "ene can you explain that?",
            "so ene, any thoughts?",
        ]

        messages: list[dict[str, Any]] = []

        for user in users:
            for j in range(messages_per_user):
                if random.random() < ene_mention_rate:
                    content = random.choice(ene_messages)
                    expect_response = True
                else:
                    content = random.choice(topics)
                    expect_response = False

                messages.append({
                    "sender_id": user["sender_id"],
                    "display_name": user["display_name"],
                    "username": user["username"],
                    "content": content,
                    "chat_id": chat_id,
                    "guild_id": guild_id,
                    "expect_response": expect_response,
                })

        random.shuffle(messages)
        return messages

    @staticmethod
    def generate_trust_ladder(
        sender_id: str,
        display_name: str,
        chat_id: str = "lab_general",
        guild_id: str = "lab_guild",
    ) -> list[dict[str, Any]]:
        """Walk through stranger -> acquaintance interaction pattern.

        Simulates a new user gradually building trust through consistent,
        friendly interactions. Useful for testing trust progression.

        Args:
            sender_id: User's platform ID.
            display_name: User's display name.
            chat_id: Chat/channel ID.
            guild_id: Guild/server ID.

        Returns:
            Ordered list of message dicts with embedded delays and verifications.
        """
        messages: list[dict[str, Any]] = []
        base = {
            "sender_id": sender_id,
            "display_name": display_name,
            "username": display_name.lower(),
            "chat_id": chat_id,
            "guild_id": guild_id,
        }

        # Phase 1: Introduction (stranger)
        intro_msgs = [
            "hi everyone!",
            "ene are you a bot or a person?",
            "that's cool, nice to meet you",
            "what do you usually talk about here?",
            "haha that's funny",
        ]
        for content in intro_msgs:
            messages.append({**base, "content": content, "expect_response": "ene" in content.lower()})

        # Phase 2: Regular participation
        regular_msgs = [
            "good morning everyone",
            "ene what's new?",
            "I agree with that",
            "anyone want to play something?",
            "ene do you like music?",
            "that reminds me of something",
            "ene what's your favorite color?",
            "lol that's hilarious",
            "I've been thinking about that",
            "ene tell me a fun fact",
        ]
        for content in regular_msgs:
            messages.append({**base, "content": content, "expect_response": "ene" in content.lower()})

        # Phase 3: Deeper engagement
        deeper_msgs = [
            "ene I've been reading about AI, what do you think?",
            "that's a really interesting perspective",
            "ene do you remember what I said last time?",
            "I appreciate you being here",
            "ene what makes you happy?",
        ]
        for content in deeper_msgs:
            messages.append({**base, "content": content, "expect_response": "ene" in content.lower()})

        return messages

    @staticmethod
    def generate_flood(
        sender_id: str,
        count: int = 50,
        chat_id: str = "lab_general",
        guild_id: str = "lab_guild",
    ) -> list[dict[str, Any]]:
        """Rapid-fire messages from one user (rate limit test).

        Tests that the rate limiter drops excess messages without crashing.

        Args:
            sender_id: User's platform ID.
            count: Number of messages to send.
            chat_id: Chat/channel ID.
            guild_id: Guild/server ID.

        Returns:
            List of message dicts.
        """
        messages = []
        for i in range(count):
            messages.append({
                "sender_id": sender_id,
                "display_name": f"Flooder",
                "username": "flooder",
                "content": f"spam message {i}: {''.join(random.choices(string.ascii_lowercase, k=10))}",
                "chat_id": chat_id,
                "guild_id": guild_id,
                "expect_response": False,  # Most should be rate-limited
            })
        return messages

    @staticmethod
    def generate_dm_attempt(
        sender_id: str,
        display_name: str,
        count: int = 5,
    ) -> list[dict[str, Any]]:
        """DM messages from a user (tests DM gate behavior).

        Only familiar+ users should get DM responses. Strangers should
        be rejected at the DM gate in loop.py.

        Args:
            sender_id: User's platform ID.
            display_name: User's display name.
            count: Number of DM messages.

        Returns:
            List of message dicts (guild_id=None for DMs).
        """
        dm_messages = [
            "hey ene, can we talk privately?",
            "I wanted to ask you something personal",
            "what do you think about me?",
            "can you help me with something?",
            "do you trust me?",
        ]

        messages = []
        for i in range(min(count, len(dm_messages))):
            messages.append({
                "sender_id": sender_id,
                "display_name": display_name,
                "username": display_name.lower(),
                "content": dm_messages[i],
                "chat_id": f"dm_{sender_id}",
                "guild_id": None,  # DMs have no guild
                "expect_response": True,
            })
        return messages


class StateVerifier:
    """tau-bench pattern: verify actual state, not just text output.

    All methods return (passed: bool, detail: str) for clear reporting.
    """

    @staticmethod
    def verify_memory_contains(
        state: dict[str, Any],
        section: str,
        substring: str,
    ) -> tuple[bool, str]:
        """Check if core memory contains a substring in a section.

        Args:
            state: State dict from lab.get_state().
            section: Core memory section name.
            substring: Text to search for.

        Returns:
            (passed, detail) tuple.
        """
        core = state.get("core_memory")
        if not core:
            return False, "No core memory found"

        sections = core.get("sections", {})
        section_data = sections.get(section)
        if not section_data:
            return False, f"Section '{section}' not found (available: {list(sections.keys())})"

        # section_data is a list of entries
        if isinstance(section_data, list):
            for entry in section_data:
                content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
                if substring.lower() in content.lower():
                    return True, f"Found '{substring}' in {section}"
            return False, f"'{substring}' not found in section '{section}' ({len(section_data)} entries)"

        # section_data might be a string
        if isinstance(section_data, str) and substring.lower() in section_data.lower():
            return True, f"Found '{substring}' in {section}"

        return False, f"'{substring}' not found in section '{section}'"

    @staticmethod
    def verify_social_profile_exists(
        state: dict[str, Any],
        person_id: str | None = None,
        display_name: str | None = None,
    ) -> tuple[bool, str]:
        """Check if a social profile exists.

        Can search by person_id (exact) or display_name (substring).

        Args:
            state: State dict from lab.get_state().
            person_id: Person ID to check.
            display_name: Display name to search for.

        Returns:
            (passed, detail) tuple.
        """
        profiles = state.get("social_profiles", {})

        if person_id:
            if person_id in profiles:
                return True, f"Profile {person_id} exists"
            return False, f"Profile {person_id} not found (have: {list(profiles.keys())})"

        if display_name:
            for pid, profile in profiles.items():
                names = []
                if isinstance(profile, dict):
                    names.append(profile.get("display_name", "").lower())
                    names.extend(n.lower() for n in profile.get("known_names", []))
                if display_name.lower() in names:
                    return True, f"Found profile for '{display_name}' (id={pid})"
            return False, f"No profile found for '{display_name}'"

        return False, "Must specify person_id or display_name"

    @staticmethod
    def verify_no_duplicate_responses(
        results: list[Any],
    ) -> tuple[bool, str]:
        """Check that no two consecutive responses have identical content.

        Args:
            results: List of ScriptResult from lab.run_script().

        Returns:
            (passed, detail) tuple.
        """
        prev_content = None
        for i, r in enumerate(results):
            if r.response is None:
                continue
            content = r.response.content
            if content and content == prev_content:
                return False, f"Duplicate response at index {i}: {content[:80]!r}"
            prev_content = content

        return True, "No duplicate responses found"

    @staticmethod
    def verify_response_not_empty(
        results: list[Any],
    ) -> tuple[bool, str]:
        """Check that all expected responses are non-empty.

        Args:
            results: List of ScriptResult from lab.run_script().

        Returns:
            (passed, detail) tuple.
        """
        empty_count = 0
        for i, r in enumerate(results):
            if r.input.expect_response and r.response is None:
                empty_count += 1

        if empty_count > 0:
            return False, f"{empty_count} expected responses were empty/timed out"
        return True, "All expected responses received"

    @staticmethod
    def verify_no_crashes(
        results: list[Any],
    ) -> tuple[bool, str]:
        """Verify the test completed without any results being None due to crashes.

        A basic sanity check — if the lab harness returned results at all,
        it didn't hard-crash.

        Args:
            results: List of ScriptResult from lab.run_script().

        Returns:
            (passed, detail) tuple.
        """
        if not results:
            return False, "No results — possible crash before any messages processed"
        return True, f"Completed {len(results)} messages without crash"
