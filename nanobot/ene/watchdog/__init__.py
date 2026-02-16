"""Watchdog — periodic self-integrity checks for Ene.

Audits diary entries and core memory for corruption:
- Wrong speaker attribution
- Hallucinated events
- Spoofing artifacts (impersonation-planted memories)
- Format issues

Runs on two schedules:
- Quick audit: on_idle (every 30 min), checks new diary entries only
- Deep audit: on_daily (4 AM), checks full diary + core memory

Alerts Dad via DM when issues are found. Auto-fixes critical diary
entries when possible.
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.ene import EneModule

if TYPE_CHECKING:
    from nanobot.agent.tools.base import Tool
    from nanobot.bus.queue import MessageBus
    from nanobot.ene import EneContext
    from nanobot.ene.watchdog.auditor import AuditReport


# Dad's DM channels — same as ObservatoryModule
DAD_DM_CHANNELS = {
    "discord": "1175414972482846813",
    "telegram": "8559611823",
}

# Timing constants
QUICK_AUDIT_IDLE_THRESHOLD = 600    # 10 min idle before triggering
QUICK_AUDIT_COOLDOWN = 1800         # 30 min between quick audits


class WatchdogModule(EneModule):
    """Ene module for periodic self-integrity audits.

    Hooks into on_idle and on_daily lifecycle events. No user-facing
    tools — this runs silently in the background and alerts Dad when
    problems are found.
    """

    def __init__(self) -> None:
        self._bus: "MessageBus | None" = None
        self._auditor = None  # WatchdogAuditor, set in initialize
        self._last_audit_time: float = 0.0
        self._enabled: bool = True

    @property
    def name(self) -> str:
        return "watchdog"

    async def initialize(self, ctx: EneContext) -> None:
        """Set up the watchdog auditor."""
        self._bus = ctx.bus

        # Check config for watchdog settings
        watchdog_config = getattr(
            getattr(ctx.config, "agents", None),
            "defaults", None,
        )
        if watchdog_config:
            watchdog_config = getattr(watchdog_config, "watchdog", None)

        if watchdog_config:
            self._enabled = getattr(watchdog_config, "enabled", True)
            model = getattr(watchdog_config, "model", None)
        else:
            model = None

        if not self._enabled:
            logger.info("Watchdog: disabled in config")
            return

        # Get observatory collector for cost tracking (optional)
        observatory = None
        try:
            obs_module = ctx.bus  # We'll get it from the registry instead
        except Exception:
            pass

        # Create the auditor
        from nanobot.ene.watchdog.auditor import WatchdogAuditor

        self._auditor = WatchdogAuditor(
            provider=ctx.provider,
            memory_dir=ctx.workspace / "memory",
            model=model,  # Falls back to default model in provider
            temperature=0.2,
            observatory=None,  # Will be wired up after observatory initializes
        )

        logger.info("Watchdog: initialized")

    def get_tools(self) -> list["Tool"]:
        """No user-facing tools — watchdog runs silently."""
        return []

    def get_context_block(self) -> str | None:
        """No system prompt injection needed."""
        return None

    async def on_idle(self, idle_seconds: float) -> None:
        """Quick audit on idle — check new diary entries.

        Triggers after 10 min idle, with 30 min cooldown between audits.
        """
        if not self._enabled or not self._auditor:
            return

        # Check idle threshold
        if idle_seconds < QUICK_AUDIT_IDLE_THRESHOLD:
            return

        # Check cooldown
        now = _time.time()
        if now - self._last_audit_time < QUICK_AUDIT_COOLDOWN:
            return

        self._last_audit_time = now

        try:
            logger.info("Watchdog: starting quick audit")
            report = await self._auditor.quick_audit()

            if report.has_issues:
                logger.warning(
                    f"Watchdog: quick audit found {len(report.issues)} issue(s), "
                    f"{report.auto_fixes_applied} auto-fixed"
                )
                await self._send_alert(report)
            else:
                logger.debug(
                    f"Watchdog: quick audit clean ({report.entries_checked} entries)"
                )
        except Exception as e:
            logger.error(f"Watchdog: quick audit failed: {e}", exc_info=True)

    async def on_daily(self) -> None:
        """Deep audit — check full diary + core memory."""
        if not self._enabled or not self._auditor:
            return

        try:
            logger.info("Watchdog: starting deep audit")
            report = await self._auditor.deep_audit()

            if report.has_issues:
                logger.warning(
                    f"Watchdog: deep audit found {len(report.issues)} issue(s), "
                    f"{report.auto_fixes_applied} auto-fixed"
                )
                await self._send_alert(report)
            else:
                logger.info(
                    f"Watchdog: deep audit clean ({report.entries_checked} entries)"
                )

            # Update last audit time
            self._last_audit_time = _time.time()

        except Exception as e:
            logger.error(f"Watchdog: deep audit failed: {e}", exc_info=True)

    async def _send_alert(self, report: "AuditReport") -> None:
        """Send audit results to Dad via DM."""
        if not self._bus or not report.has_issues:
            return

        from nanobot.bus.events import OutboundMessage

        alert_text = report.format_alert()
        if not alert_text:
            return

        # Try Discord first, then Telegram (same pattern as ObservatoryModule)
        for channel, chat_id in DAD_DM_CHANNELS.items():
            try:
                await self._bus.publish_outbound(OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=alert_text,
                ))
                logger.debug(f"Watchdog: alert sent via {channel}")
                return  # Sent successfully
            except Exception as e:
                logger.debug(f"Watchdog: failed to send alert via {channel}: {e}")

        logger.warning("Watchdog: could not deliver alert to Dad")

    async def shutdown(self) -> None:
        """Cleanup — nothing to close."""
        logger.debug("Watchdog: shutdown")
