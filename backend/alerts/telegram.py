"""
Telegram alert service.

Sends alerts to a Telegram chat via the Bot API.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ..config.schema import TelegramConfig
from ..utils.logging import get_logger
from .base import AlertService, AlertSeverity, NullAlertService

logger = get_logger(__name__)


# Emoji mapping for severity levels
SEVERITY_EMOJI = {
    AlertSeverity.INFO: "\U0001F7E2",      # Green circle
    AlertSeverity.WARNING: "\U0001F7E1",   # Yellow circle
    AlertSeverity.CRITICAL: "\U0001F534",  # Red circle
}


class TelegramAlertService(AlertService):
    """
    Telegram Bot API alert service.

    Features:
    - Three severity levels with different formatting
    - Rate limiting to prevent spam
    - Retry on transient failures
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        send_info: bool = True,
        send_warning: bool = True,
        send_critical: bool = True,
    ):
        """
        Initialize Telegram alert service.

        Args:
            bot_token: Telegram bot token
            chat_id: Chat ID to send messages to
            send_info: Whether to send INFO level alerts
            send_warning: Whether to send WARNING level alerts
            send_critical: Whether to send CRITICAL level alerts
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.send_info = send_info
        self.send_warning = send_warning
        self.send_critical = send_critical

        self._base_url = f"https://api.telegram.org/bot{bot_token}"

        # Rate limiting
        self._last_message_time: Optional[datetime] = None
        self._min_interval_seconds = 1  # Minimum seconds between messages

    def _should_send(self, severity: AlertSeverity) -> bool:
        """Check if this severity level should be sent."""
        if severity == AlertSeverity.INFO and not self.send_info:
            return False
        if severity == AlertSeverity.WARNING and not self.send_warning:
            return False
        if severity == AlertSeverity.CRITICAL and not self.send_critical:
            return False
        return True

    def _format_message(
        self,
        severity: AlertSeverity,
        title: str,
        message: str,
    ) -> str:
        """Format the alert message for Telegram."""
        emoji = SEVERITY_EMOJI.get(severity, "")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        return (
            f"{emoji} <b>{severity.value} | {title}</b>\n"
            f"\n"
            f"{message}\n"
            f"\n"
            f"<i>{timestamp}</i>"
        )

    async def _rate_limit(self) -> None:
        """Apply rate limiting between messages."""
        if self._last_message_time:
            elapsed = (datetime.now(timezone.utc) - self._last_message_time).total_seconds()
            if elapsed < self._min_interval_seconds:
                await asyncio.sleep(self._min_interval_seconds - elapsed)

        self._last_message_time = datetime.now(timezone.utc)

    async def send(
        self,
        severity: AlertSeverity,
        title: str,
        message: str,
    ) -> bool:
        """
        Send an alert via Telegram.

        Args:
            severity: Alert severity level
            title: Alert title
            message: Alert message body

        Returns:
            True if sent successfully
        """
        if not self._should_send(severity):
            logger.debug("alert_skipped", severity=severity.value, title=title)
            return True

        await self._rate_limit()

        text = self._format_message(severity, title, message)

        # Disable notification for INFO level
        disable_notification = severity == AlertSeverity.INFO

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_notification": disable_notification,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        logger.debug(
                            "alert_sent",
                            severity=severity.value,
                            title=title,
                        )
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(
                            "telegram_send_failed",
                            status=response.status,
                            error=error_text,
                        )
                        return False

        except asyncio.TimeoutError:
            logger.error("telegram_timeout", title=title)
            return False
        except aiohttp.ClientError as e:
            logger.error("telegram_client_error", error=str(e))
            return False
        except Exception as e:
            logger.exception("telegram_error", error=str(e))
            return False

    async def test_connection(self) -> bool:
        """Test the Telegram bot connection."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._base_url}/getMe",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        bot_name = data.get("result", {}).get("username", "Unknown")
                        logger.info("telegram_connection_ok", bot=bot_name)
                        return True
                    else:
                        logger.error("telegram_connection_failed", status=response.status)
                        return False

        except Exception as e:
            logger.error("telegram_connection_error", error=str(e))
            return False


def create_alert_service(config: TelegramConfig) -> AlertService:
    """
    Create an alert service from configuration.

    Args:
        config: Telegram configuration

    Returns:
        AlertService instance (TelegramAlertService or NullAlertService)
    """
    if not config.enabled:
        logger.info("alerts_disabled")
        return NullAlertService()

    if not config.bot_token.get_secret_value() or not config.chat_id:
        logger.warning("telegram_config_incomplete")
        return NullAlertService()

    return TelegramAlertService(
        bot_token=config.bot_token.get_secret_value(),
        chat_id=config.chat_id,
        send_info=config.send_info,
        send_warning=config.send_warning,
        send_critical=config.send_critical,
    )
