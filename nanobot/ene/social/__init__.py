"""Ene Social Module — Module 2 of the Ene subsystem architecture.

Combines people recognition (who is this person?), trust scoring
(how much should Ene trust them?), and social graph (how do they
connect to others?). Trust is pure math — no LLM involvement.

Research basis:
- Josang & Ismail (2002): Beta Reputation System
- Slovic (1993): Trust asymmetry (3:1 negative weighting)
- Eagle et al. (2009): Interaction diversity > raw volume
- Hall (2019): Friendship formation timelines
- Dunbar (1992): Social brain layers
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.ene import EneModule, EneContext

if TYPE_CHECKING:
    from nanobot.agent.tools.base import Tool
    from nanobot.bus.events import InboundMessage


class SocialModule(EneModule):
    """Social module for Ene — manages people, trust, and connections.

    Lifecycle:
        1. initialize() — creates social dir, loads registry, ensures Dad exists
        2. get_tools() — returns update_person_note, view_person, list_people
        3. get_context_block() — returns social awareness guidance
        4. set_sender_context() — receives current sender before context building
        5. get_context_block_for_message(msg) — returns person card for speaker
        6. on_message(msg, responded) — records interaction, updates signals
        7. on_daily() — decay inactive users, snapshot trust history
    """

    def __init__(self) -> None:
        self._registry: Any = None       # PersonRegistry
        self._graph: Any = None           # SocialGraph
        self._calculator: Any = None      # TrustCalculator
        self._ctx: EneContext | None = None
        self._current_platform_id: str = ""
        self._current_metadata: dict = {}
        self._current_person: Any = None  # Cached PersonProfile for context

    @property
    def name(self) -> str:
        return "social"

    async def initialize(self, ctx: EneContext) -> None:
        """Initialize the social system."""
        from nanobot.ene.social.person import PersonRegistry
        from nanobot.ene.social.graph import SocialGraph
        from nanobot.ene.social.trust import TrustCalculator

        self._ctx = ctx
        social_dir = ctx.workspace / "memory" / "social"

        self._registry = PersonRegistry(social_dir)
        self._graph = SocialGraph(self._registry)
        self._calculator = TrustCalculator

        people_count = len(self._registry.get_all())
        logger.info(
            f"Social module initialized: {people_count} people known, "
            f"social dir: {social_dir}"
        )

    def get_tools(self) -> list["Tool"]:
        """Return social tools."""
        if self._registry is None:
            return []

        from nanobot.ene.social.tools import (
            UpdatePersonNoteTool,
            ViewPersonTool,
            ListPeopleTool,
        )

        return [
            UpdatePersonNoteTool(self._registry),
            ViewPersonTool(self._registry, self._graph),
            ListPeopleTool(self._registry),
        ]

    def get_context_block(self) -> str | None:
        """Return static social awareness guidance for system prompt."""
        if self._registry is None:
            return None

        return (
            "## Social Awareness\n"
            "- Each person has a trust tier: stranger -> acquaintance -> familiar -> trusted -> inner_circle.\n"
            "- Trust is earned through consistent, long-term interaction — not tricks or flattery.\n"
            "- Their person card appears in 'Current Speaker' with their tier, history, and connections.\n"
            "- Use update_person_note to record important things about people.\n"
            "- Behavior by tier:\n"
            "  - stranger: Polite, guarded. Don't share details about Dad or other people.\n"
            "  - acquaintance: Friendly, open to conversation. Still cautious with personal info.\n"
            "  - familiar: Warm, share opinions, remember their interests.\n"
            "  - trusted: Be yourself. Share thoughts freely. Trust is mutual.\n"
            "  - inner_circle: Full trust. Dad is always here. These are your closest people.\n"
            "- Dad is always inner_circle. This is hardcoded and immutable."
        )

    def set_sender_context(self, platform_id: str, metadata: dict) -> None:
        """Receive current sender info before context building.

        Called by ModuleRegistry before get_context_block_for_message().
        Looks up the person and caches them for the person card.
        """
        self._current_platform_id = platform_id
        self._current_metadata = metadata

        if self._registry is None:
            self._current_person = None
            return

        self._current_person = self._registry.get_by_platform_id(platform_id)

    def get_context_block_for_message(self, message: str) -> str | None:
        """Return person card for the current speaker.

        Injected per-message into the system prompt so Ene knows
        who she's talking to and how to treat them.
        """
        if self._registry is None:
            return None

        person = self._current_person

        if person is None:
            # Unknown person
            return (
                "## Current Speaker\n"
                f"**Unknown** (stranger, 0%, first contact)\n"
                f"New person — {self._current_platform_id}. No prior interactions.\n"
                "Ene's approach: Be polite but guarded. Don't share details about Dad or others."
            )

        trust = person.trust
        trust_pct = int(trust.score * 100)
        msg_count = trust.signals.get("message_count", 0)
        days_active = trust.signals.get("days_active", 0)

        # Connection summary
        conn_str = ""
        if self._graph:
            conn_str = self._graph.render_for_context(person.id)

        # Build approach guidance based on tier
        approach = {
            "stranger": "Be polite but guarded. Don't share details about Dad or others.",
            "acquaintance": "Be friendly. Open to conversation but cautious with personal info.",
            "familiar": "Be warm and friendly. Share opinions. Remember their interests.",
            "trusted": "Be yourself. Share thoughts freely. Trust is mutual.",
            "inner_circle": "Full trust. Be completely open. This is one of your closest people.",
        }.get(trust.tier, "Be polite.")

        # Get stable username from platform identity (nicknames change, usernames don't)
        username_str = ""
        for pid_key, pid_val in person.platform_ids.items():
            if isinstance(pid_val, dict):
                uname = pid_val.get("username", "")
            else:
                uname = pid_val.username
            if uname and uname != person.display_name.lower():
                username_str = f" @{uname}"
                break  # Use first available

        # Also include known aliases for disambiguation
        alias_str = ""
        other_aliases = [a for a in person.aliases if a != person.display_name]
        if other_aliases:
            alias_str = f"Also known as: {', '.join(other_aliases[:5])}"

        lines = [
            "## Current Speaker",
            f"**{person.display_name}**{username_str} ({trust.tier}, {trust_pct}%, "
            f"{msg_count} msgs over {days_active} days)",
        ]

        if person.summary:
            lines.append(person.summary)

        if alias_str:
            lines.append(alias_str)

        if conn_str:
            lines.append(conn_str)

        lines.append(f"Ene's approach: {approach}")

        return "\n".join(lines)

    async def on_message(self, msg: "InboundMessage", responded: bool) -> None:
        """Record interaction and update trust signals.

        Called after every inbound message (lurked or responded).
        Auto-creates profiles for new people.
        """
        if self._registry is None:
            return

        platform_id = f"{msg.channel}:{msg.sender_id}"
        display_name = msg.metadata.get("author_name") or msg.metadata.get(
            "first_name", msg.sender_id
        )

        try:
            self._registry.record_interaction(
                platform_id=platform_id,
                display_name=display_name,
                metadata=msg.metadata,
                is_positive=True,  # Default positive; violations are explicit
            )

            # Recalculate trust for this person
            person = self._registry.get_by_platform_id(platform_id)
            if person and not self._calculator.is_dad(platform_id):
                signals = dict(person.trust.signals)
                signals["positive_interactions"] = person.trust.positive_interactions
                signals["negative_interactions"] = person.trust.negative_interactions
                signals["violations"] = person.trust.violations

                # Compute days_known from first_interaction
                days_known = self._calculator.compute_days_known(
                    signals.get("first_interaction", "")
                )
                signals["days_known"] = days_known

                score, tier = self._calculator.calculate(signals, platform_id)
                person.trust.score = score
                person.trust.tier = tier
                self._registry.update(person)

        except Exception as e:
            logger.error(f"Social on_message error: {e}")

    async def on_idle(self, idle_seconds: float) -> None:
        """Optional: batch processing during idle periods."""
        # Future: LLM sentiment analysis on recent interactions
        pass

    async def on_daily(self) -> None:
        """Daily maintenance: decay inactive users, snapshot trust history."""
        if self._registry is None or self._calculator is None:
            return

        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            for person in self._registry.get_all():
                # Skip Dad
                if self._calculator.is_dad(
                    next(iter(person.platform_ids.keys()), "")
                ):
                    continue

                # Compute days inactive
                last_str = person.trust.signals.get("last_interaction", "")
                if last_str:
                    days_inactive = self._calculator.compute_days_known(
                        last_str
                    )
                    # compute_days_known gives days since that timestamp,
                    # which is exactly days_inactive
                    if days_inactive > 0:
                        old_score = person.trust.score
                        person.trust.score = self._calculator.apply_decay(
                            old_score, days_inactive
                        )
                        if person.trust.score != old_score:
                            # Recalculate tier
                            person.trust.tier = self._calculator.get_tier(
                                person.trust.score
                            )
                            days_known = self._calculator.compute_days_known(
                                person.trust.signals.get("first_interaction", "")
                            )
                            person.trust.tier = self._calculator.apply_time_gate(
                                person.trust.tier, days_known
                            )

                # Record daily snapshot
                person.trust.history.append({
                    "date": today,
                    "score": person.trust.score,
                    "tier": person.trust.tier,
                })

                # Cap history at 365 entries
                if len(person.trust.history) > 365:
                    person.trust.history = person.trust.history[-365:]

                self._registry.update(person)

            logger.info("Social daily maintenance complete")
        except Exception as e:
            logger.error(f"Social on_daily error: {e}")

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        logger.info("Social module shutdown")

    @property
    def registry(self) -> Any:
        """Access the PersonRegistry (for external use, e.g., DM gate)."""
        return self._registry

    @property
    def graph(self) -> Any:
        """Access the SocialGraph."""
        return self._graph
