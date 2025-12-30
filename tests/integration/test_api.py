"""
Integration tests for the API layer.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    @pytest.mark.asyncio
    async def test_health_check(self, async_client: AsyncClient):
        """Test the health check endpoint."""
        response = await async_client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "database" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_readiness_check(self, async_client: AsyncClient):
        """Test the readiness endpoint."""
        response = await async_client.get("/api/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True

    @pytest.mark.asyncio
    async def test_liveness_check(self, async_client: AsyncClient):
        """Test the liveness endpoint."""
        response = await async_client.get("/api/live")
        assert response.status_code == 200
        data = response.json()
        assert data["alive"] is True


class TestRootEndpoint:
    """Tests for root endpoint."""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, async_client: AsyncClient):
        """Test the root endpoint."""
        response = await async_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Funding Rate Arbitrage API"
        assert data["version"] == "1.0.0"
        assert "docs" in data


class TestPositionsEndpoints:
    """Tests for position management endpoints."""

    @pytest.mark.asyncio
    async def test_get_positions(self, async_client: AsyncClient):
        """Test getting all positions."""
        response = await async_client.get("/api/positions")
        assert response.status_code == 200
        data = response.json()
        assert "positions" in data
        assert "total" in data
        assert isinstance(data["positions"], list)

    @pytest.mark.asyncio
    async def test_get_open_positions(self, async_client: AsyncClient):
        """Test getting open positions."""
        response = await async_client.get("/api/positions/open")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_closed_positions(self, async_client: AsyncClient):
        """Test getting closed positions."""
        response = await async_client.get("/api/positions/closed")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_positions_with_pagination(self, async_client: AsyncClient):
        """Test position pagination."""
        response = await async_client.get("/api/positions?limit=10&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert "positions" in data

    @pytest.mark.asyncio
    async def test_get_position_not_found(self, async_client: AsyncClient):
        """Test getting non-existent position."""
        response = await async_client.get("/api/positions/nonexistent-id")
        assert response.status_code == 404


class TestEngineEndpoints:
    """Tests for trading engine endpoints."""

    @pytest.mark.asyncio
    async def test_get_engine_status(self, async_client: AsyncClient):
        """Test getting engine status."""
        response = await async_client.get("/api/engine/status")
        assert response.status_code == 200
        data = response.json()
        assert "state" in data
        assert "simulation_mode" in data
        assert "connected_exchanges" in data

    @pytest.mark.asyncio
    async def test_get_risk_status(self, async_client: AsyncClient):
        """Test getting risk status."""
        response = await async_client.get("/api/engine/risk")
        assert response.status_code == 200
        data = response.json()
        assert "kill_switch_active" in data
        assert "trading_enabled" in data

    @pytest.mark.asyncio
    async def test_get_stats(self, async_client: AsyncClient):
        """Test getting trading stats."""
        response = await async_client.get("/api/engine/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_positions" in data
        assert "open_positions" in data
        assert "total_realized_pnl" in data

    @pytest.mark.asyncio
    async def test_start_engine_not_implemented(self, async_client: AsyncClient):
        """Test starting engine (not fully implemented)."""
        response = await async_client.post("/api/engine/start")
        # Returns 501 because coordinator is not wired
        assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_kill_switch_requires_confirm(self, async_client: AsyncClient):
        """Test that kill switch requires confirmation."""
        response = await async_client.post(
            "/api/engine/kill",
            json={"confirm": False}
        )
        assert response.status_code == 400


class TestConfigEndpoints:
    """Tests for configuration endpoints."""

    @pytest.mark.asyncio
    async def test_get_config(self, async_client: AsyncClient):
        """Test getting configuration."""
        response = await async_client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert "symbols" in data
        assert "simulation_mode" in data

    @pytest.mark.asyncio
    async def test_get_supported_exchanges(self, async_client: AsyncClient):
        """Test getting supported exchanges."""
        response = await async_client.get("/api/config/supported-exchanges")
        assert response.status_code == 200
        data = response.json()
        assert "exchanges" in data
        assert isinstance(data["exchanges"], list)

    @pytest.mark.asyncio
    async def test_get_leverage_config(self, async_client: AsyncClient):
        """Test getting leverage config for an exchange."""
        response = await async_client.get("/api/config/leverage/binance")
        assert response.status_code == 200
        data = response.json()
        assert "exchange" in data
        assert data["exchange"] == "binance"


class TestAPIErrorHandling:
    """Tests for API error handling."""

    @pytest.mark.asyncio
    async def test_invalid_status_filter(self, async_client: AsyncClient):
        """Test invalid status filter doesn't crash."""
        response = await async_client.get("/api/positions?status=invalid")
        # Should handle gracefully
        assert response.status_code in [200, 400]

    @pytest.mark.asyncio
    async def test_invalid_limit_value(self, async_client: AsyncClient):
        """Test invalid limit parameter."""
        response = await async_client.get("/api/positions?limit=-1")
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_invalid_offset_value(self, async_client: AsyncClient):
        """Test invalid offset parameter."""
        response = await async_client.get("/api/positions?offset=-1")
        assert response.status_code == 422  # Validation error
