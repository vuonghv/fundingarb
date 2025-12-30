"""
Base alert interface.

Defines the abstract interface for alert services.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional


class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertService(ABC):
    """
    Abstract base class for alert services.

    All alert implementations must inherit from this class.
    """

    @abstractmethod
    async def send(
        self,
        severity: AlertSeverity,
        title: str,
        message: str,
    ) -> bool:
        """
        Send an alert.

        Args:
            severity: Alert severity level
            title: Alert title
            message: Alert message body

        Returns:
            True if sent successfully
        """
        pass

    async def send_info(self, title: str, message: str) -> bool:
        """Send an INFO level alert."""
        return await self.send(AlertSeverity.INFO, title, message)

    async def send_warning(self, title: str, message: str) -> bool:
        """Send a WARNING level alert."""
        return await self.send(AlertSeverity.WARNING, title, message)

    async def send_critical(self, title: str, message: str) -> bool:
        """Send a CRITICAL level alert."""
        return await self.send(AlertSeverity.CRITICAL, title, message)

    @abstractmethod
    async def test_connection(self) -> bool:
        """
        Test the alert service connection.

        Returns:
            True if connection is working
        """
        pass


class NullAlertService(AlertService):
    """
    Null alert service that does nothing.

    Used when alerting is disabled.
    """

    async def send(
        self,
        severity: AlertSeverity,
        title: str,
        message: str,
    ) -> bool:
        # Log locally but don't send anywhere
        return True

    async def test_connection(self) -> bool:
        return True
