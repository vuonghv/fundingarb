"""
Integration tests for the API layer.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock


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
    async def test_health_check_includes_exchanges_field(self, async_client: AsyncClient):
        """Test that health check includes exchanges field."""
        response = await async_client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "exchanges" in data
        assert isinstance(data["exchanges"], dict)

    @pytest.mark.asyncio
    async def test_health_check_includes_engine_running_field(self, async_client: AsyncClient):
        """Test that health check includes engine_running field."""
        response = await async_client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "engine_running" in data
        assert isinstance(data["engine_running"], bool)

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


class TestHealthStatusConditions:
    """Tests for health check status conditions (healthy/degraded/unhealthy)."""

    @pytest.mark.asyncio
    async def test_health_status_healthy_all_components_up(self, mock_config):
        """Test healthy status when db, engine, and all exchanges are up."""
        from backend.api.server import create_app
        from backend.database.connection import init_database, close_database
        import os

        test_db_path = "/tmp/test_health_healthy.db"
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

        mock_config.database.sqlite_path = test_db_path
        await init_database(mock_config.database)

        # Create mock coordinator
        mock_coordinator = MagicMock()
        mock_coordinator.is_running = True

        # Create mock exchanges (all connected)
        mock_exchange1 = MagicMock()
        mock_exchange1.is_connected = True
        mock_exchange1._circuit_breaker_open = False

        mock_exchange2 = MagicMock()
        mock_exchange2.is_connected = True
        mock_exchange2._circuit_breaker_open = False

        app = create_app(
            config=mock_config,
            coordinator=mock_coordinator,
            exchanges={"binance": mock_exchange1, "bybit": mock_exchange2},
        )
        app.state.config = mock_config
        app.state.coordinator = mock_coordinator
        app.state.exchanges = {"binance": mock_exchange1, "bybit": mock_exchange2}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert data["database"] is True
            assert data["engine_running"] is True
            assert data["exchanges"]["binance"]["connected"] is True
            assert data["exchanges"]["bybit"]["connected"] is True

        await close_database()
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

    @pytest.mark.asyncio
    async def test_health_status_degraded_engine_not_running(self, mock_config):
        """Test degraded status when engine is not running but exchanges connected."""
        from backend.api.server import create_app
        from backend.database.connection import init_database, close_database
        import os

        test_db_path = "/tmp/test_health_degraded_engine.db"
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

        mock_config.database.sqlite_path = test_db_path
        await init_database(mock_config.database)

        # Create mock coordinator (not running)
        mock_coordinator = MagicMock()
        mock_coordinator.is_running = False

        # Create mock exchanges (all connected)
        mock_exchange = MagicMock()
        mock_exchange.is_connected = True
        mock_exchange._circuit_breaker_open = False

        app = create_app(
            config=mock_config,
            coordinator=mock_coordinator,
            exchanges={"binance": mock_exchange},
        )
        app.state.config = mock_config
        app.state.coordinator = mock_coordinator
        app.state.exchanges = {"binance": mock_exchange}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "degraded"
            assert data["database"] is True
            assert data["engine_running"] is False

        await close_database()
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

    @pytest.mark.asyncio
    async def test_health_status_degraded_exchange_disconnected(self, mock_config):
        """Test degraded status when an exchange is disconnected."""
        from backend.api.server import create_app
        from backend.database.connection import init_database, close_database
        import os

        test_db_path = "/tmp/test_health_degraded_exchange.db"
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

        mock_config.database.sqlite_path = test_db_path
        await init_database(mock_config.database)

        # Create mock coordinator (running)
        mock_coordinator = MagicMock()
        mock_coordinator.is_running = True

        # Create mock exchanges (one disconnected)
        mock_exchange1 = MagicMock()
        mock_exchange1.is_connected = True
        mock_exchange1._circuit_breaker_open = False

        mock_exchange2 = MagicMock()
        mock_exchange2.is_connected = False  # Disconnected
        mock_exchange2._circuit_breaker_open = False

        app = create_app(
            config=mock_config,
            coordinator=mock_coordinator,
            exchanges={"binance": mock_exchange1, "bybit": mock_exchange2},
        )
        app.state.config = mock_config
        app.state.coordinator = mock_coordinator
        app.state.exchanges = {"binance": mock_exchange1, "bybit": mock_exchange2}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "degraded"
            assert data["exchanges"]["binance"]["connected"] is True
            assert data["exchanges"]["bybit"]["connected"] is False

        await close_database()
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

    @pytest.mark.asyncio
    async def test_health_status_degraded_no_exchanges(self, mock_config):
        """Test degraded status when no exchanges are configured."""
        from backend.api.server import create_app
        from backend.database.connection import init_database, close_database
        import os

        test_db_path = "/tmp/test_health_degraded_no_ex.db"
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

        mock_config.database.sqlite_path = test_db_path
        await init_database(mock_config.database)

        # No coordinator, no exchanges
        app = create_app(
            config=mock_config,
            coordinator=None,
            exchanges={},
        )
        app.state.config = mock_config
        app.state.coordinator = None
        app.state.exchanges = {}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            # DB healthy but no exchanges and no engine = degraded
            assert data["status"] == "degraded"
            assert data["database"] is True
            assert data["engine_running"] is False
            assert data["exchanges"] == {}

        await close_database()
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

    @pytest.mark.asyncio
    async def test_health_check_exchange_circuit_breaker_status(self, mock_config):
        """Test that health check reports circuit breaker status."""
        from backend.api.server import create_app
        from backend.database.connection import init_database, close_database
        import os

        test_db_path = "/tmp/test_health_circuit.db"
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

        mock_config.database.sqlite_path = test_db_path
        await init_database(mock_config.database)

        # Create mock exchange with circuit breaker open
        mock_exchange = MagicMock()
        mock_exchange.is_connected = True
        mock_exchange._circuit_breaker_open = True

        app = create_app(
            config=mock_config,
            coordinator=None,
            exchanges={"binance": mock_exchange},
        )
        app.state.config = mock_config
        app.state.coordinator = None
        app.state.exchanges = {"binance": mock_exchange}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            data = response.json()
            assert data["exchanges"]["binance"]["circuit_breaker_open"] is True

        await close_database()
        if os.path.exists(test_db_path):
            os.remove(test_db_path)


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
    async def test_start_engine_no_coordinator(self, async_client: AsyncClient):
        """Test starting engine without coordinator returns 503."""
        response = await async_client.post("/api/engine/start")
        # Returns 503 because coordinator is not initialized in test
        assert response.status_code == 503
        assert "coordinator" in response.json()["detail"].lower()

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


class TestEngineRatesEndpoint:
    """Tests for the /api/engine/rates endpoint."""

    @pytest.mark.asyncio
    async def test_get_funding_rates_no_exchanges(self, async_client: AsyncClient):
        """Test getting funding rates when no exchanges connected."""
        response = await async_client.get("/api/engine/rates")
        assert response.status_code == 200
        data = response.json()
        assert "rates" in data
        # Without exchanges, should return empty or message
        assert isinstance(data["rates"], list)

    @pytest.mark.asyncio
    async def test_get_funding_rates_response_structure(self, async_client: AsyncClient):
        """Test that rates response has expected structure."""
        response = await async_client.get("/api/engine/rates")
        assert response.status_code == 200
        data = response.json()
        assert "rates" in data


class TestEngineOpportunitiesEndpoint:
    """Tests for the /api/engine/opportunities endpoint."""

    @pytest.mark.asyncio
    async def test_get_opportunities(self, async_client: AsyncClient):
        """Test getting current opportunities."""
        response = await async_client.get("/api/engine/opportunities")
        assert response.status_code == 200
        data = response.json()
        assert "opportunities" in data

    @pytest.mark.asyncio
    async def test_get_opportunities_empty(self, async_client: AsyncClient):
        """Test getting opportunities when none exist."""
        response = await async_client.get("/api/engine/opportunities")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["opportunities"], list)


class TestEngineScanEndpoint:
    """Tests for the /api/engine/scan endpoint."""

    @pytest.mark.asyncio
    async def test_force_scan_no_coordinator(self, async_client: AsyncClient):
        """Test force scan without coordinator returns 503."""
        response = await async_client.post("/api/engine/scan")
        # Returns 503 because coordinator is not initialized in test
        assert response.status_code == 503
        assert "coordinator" in response.json()["detail"].lower()


class TestEngineStopEndpoint:
    """Tests for the /api/engine/stop endpoint."""

    @pytest.mark.asyncio
    async def test_stop_engine_no_coordinator(self, async_client: AsyncClient):
        """Test stopping engine without coordinator returns 503."""
        response = await async_client.post("/api/engine/stop")
        # Returns 503 because coordinator is not initialized in test
        assert response.status_code == 503
        assert "coordinator" in response.json()["detail"].lower()


class TestEngineKillSwitchEndpoints:
    """Tests for kill switch endpoints."""

    @pytest.mark.asyncio
    async def test_kill_switch_no_coordinator(self, async_client: AsyncClient):
        """Test kill switch without coordinator returns 503."""
        response = await async_client.post(
            "/api/engine/kill",
            json={"confirm": True, "reason": "Test activation"}
        )
        # Returns 503 because coordinator is not initialized in test
        assert response.status_code == 503
        assert "coordinator" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_deactivate_kill_switch_no_coordinator(self, async_client: AsyncClient):
        """Test deactivating kill switch without coordinator returns 503."""
        response = await async_client.post("/api/engine/kill/deactivate")
        # Returns 503 because coordinator is not initialized in test
        assert response.status_code == 503
        assert "coordinator" in response.json()["detail"].lower()


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
