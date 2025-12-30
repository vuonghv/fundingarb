# Funding Rate Arbitrage Backend - Implementation Plan

## Overview

Build a production-ready Python backend for the existing Bloomberg-style dashboard. The backend will connect to Binance and Bybit, scan for funding rate arbitrage opportunities, execute hedged positions, and serve real-time data via WebSocket.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Web Framework | **FastAPI** | Async native, auto OpenAPI docs, built-in WebSocket, Pydantic validation |
| Database Migrations | **Alembic** | Industry standard for SQLAlchemy, version-controlled schema |
| Secrets Encryption | **Fernet** | Simple, well-tested, PBKDF2 key derivation |
| WebSocket Protocol | **Typed JSON events** | Matches dashboard expectations, easy debugging |

---

## Implementation Phases

### Phase 1: Foundation & Configuration

**Files to create:**
```
backend/__init__.py
backend/main.py                    # Entry point with graceful shutdown
backend/config/__init__.py
backend/config/loader.py           # Config loading with decryption
backend/config/schema.py           # Pydantic config models
backend/utils/__init__.py
backend/utils/encryption.py        # Fernet encryption utilities
backend/utils/logging.py           # Structured logging (structlog)
config/config.example.yaml         # Example configuration
requirements.txt
```

---

### Phase 2: Database Layer

**Files to create:**
```
backend/database/__init__.py
backend/database/connection.py     # Async session management
backend/database/models.py         # Position, Trade, FundingEvent models
backend/database/repository.py     # Repository pattern for data access
backend/database/migrations/       # Alembic setup
```

**Key models (matching SPEC.md schema):**
- `Position` - Tracks hedged positions across exchange pairs
- `Trade` - Individual order executions
- `FundingEvent` - Funding payments received

---

### Phase 3: Exchange Abstraction Layer

**Files to create:**
```
backend/exchanges/__init__.py
backend/exchanges/types.py         # FundingRate, OrderBook, Order, OrderResult
backend/exchanges/base.py          # Abstract ExchangeAdapter with circuit breaker
backend/exchanges/factory.py       # Exchange factory
backend/exchanges/binance.py       # Binance USDT-M futures via ccxt
backend/exchanges/bybit.py         # Bybit linear perpetuals via ccxt
```

**Key patterns:**
- Circuit breaker: 5 consecutive failures → pause exchange operations
- Rate limit respect with request queuing
- WebSocket subscriptions for real-time funding rate updates
- Testnet support for simulation mode

---

### Phase 4: Trading Engine Core

**Files to create:**
```
backend/engine/__init__.py
backend/engine/scanner.py          # Event-driven funding rate scanner
backend/engine/detector.py         # Arbitrage opportunity detection
backend/engine/executor.py         # Order execution with leg ordering
backend/engine/position_manager.py # Position lifecycle management
backend/engine/risk_manager.py     # Kill switch, liquidation handling
backend/engine/coordinator.py      # Main engine coordinator
```

**Critical logic in executor.py:**
1. Execute lower liquidity exchange first
2. If first leg fills, execute second leg
3. If second leg fails → immediately market close first leg
4. Limit orders at mid-price with configurable timeout

**Spread threshold formula:**
```
threshold = 0.01% + (0.001% × position_size_usd / 10,000)
```

---

### Phase 5: Alert System

**Files to create:**
```
backend/alerts/__init__.py
backend/alerts/base.py             # Alert interface
backend/alerts/telegram.py         # Telegram bot integration
```

**Severity levels:**
- INFO: Position opened/closed, funding collected
- WARNING: Spread widening, connectivity issues
- CRITICAL: Liquidation, kill switch, system errors

---

### Phase 6: API Layer

**Files to create:**
```
backend/api/__init__.py
backend/api/server.py              # FastAPI app with lifespan management
backend/api/schemas.py             # Pydantic response models
backend/api/websocket.py           # WebSocket manager with broadcast
backend/api/routes/__init__.py
backend/api/routes/positions.py    # GET/POST positions, close
backend/api/routes/engine.py       # start/stop/kill endpoints
backend/api/routes/config.py       # Hot reload configuration
backend/api/routes/health.py       # Health check endpoint
```

**REST Endpoints:**
```
POST /api/positions/open
POST /api/positions/{id}/close
POST /api/engine/start
POST /api/engine/stop
POST /api/engine/kill
POST /api/config/update
GET  /api/status
GET  /api/positions
GET  /api/health
```

**WebSocket Events (matching SPEC.md):**
- `POSITION_UPDATE` - Position state changes
- `FUNDING_RATE_UPDATE` - Real-time rate updates
- `TRADE_EXECUTED` - Order fills
- `ENGINE_STATUS` - Engine state changes
- `ALERT` - Alert notifications

---

### Phase 7: Dashboard Integration

**Modify:** `index.html` → move to `dashboard/index.html`

**Changes:**
- Replace mock data with API fetches
- Add WebSocket connection with exponential backoff reconnection
- Add new panels:
  - Panel 7: Trading Engine Status
  - Panel 8: Quick Actions (Start/Stop, Kill Switch)
  - Panel 9: Position Entry Form
  - Panel 10: Configuration Editor

---

### Phase 8: Testing

**Files to create:**
```
tests/__init__.py
tests/conftest.py                  # Pytest fixtures
tests/unit/test_config.py
tests/unit/test_encryption.py
tests/unit/test_detector.py
tests/unit/test_executor.py
tests/unit/test_risk_manager.py
tests/integration/test_exchanges.py # Testnet integration
tests/integration/test_api.py
```

**Simulation mode:**
- Force `testnet=True` on all exchanges
- Require 24h simulation before allowing live trading
- Dashboard shows `[SIMULATION]` badge

---

### Phase 9: Production Hardening

**Files to create:**
```
docker/Dockerfile
docker/docker-compose.yml
scripts/encrypt_config.py          # CLI tool to encrypt config
```

**Key features:**
- Graceful shutdown with checkpoint save
- Signal handlers (SIGTERM, SIGINT)
- Health check endpoint for monitoring
- Alembic migrations for schema updates

---

## Final File Structure

```
fundingarb/
├── backend/
│   ├── __init__.py
│   ├── main.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── loader.py
│   │   └── schema.py
│   ├── exchanges/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── base.py
│   │   ├── factory.py
│   │   ├── binance.py
│   │   └── bybit.py
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── scanner.py
│   │   ├── detector.py
│   │   ├── executor.py
│   │   ├── position_manager.py
│   │   ├── risk_manager.py
│   │   └── coordinator.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── server.py
│   │   ├── websocket.py
│   │   ├── schemas.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── positions.py
│   │       ├── engine.py
│   │       ├── config.py
│   │       └── health.py
│   ├── alerts/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   └── telegram.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py
│   │   ├── models.py
│   │   ├── repository.py
│   │   └── migrations/
│   │       └── versions/
│   └── utils/
│       ├── __init__.py
│       ├── encryption.py
│       └── logging.py
├── dashboard/
│   └── index.html
├── config/
│   └── config.example.yaml
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   └── integration/
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── scripts/
│   └── encrypt_config.py
├── requirements.txt
├── SPEC.md
├── CLAUDE.md
├── PLAN.md
└── README.md
```

---

## Dependencies

```txt
# Core
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
pydantic>=2.5.0
pydantic-settings>=2.1.0

# Database
sqlalchemy[asyncio]>=2.0.25
alembic>=1.13.0
asyncpg>=0.29.0
aiosqlite>=0.19.0

# Exchange connectivity
ccxt>=4.2.0

# WebSocket and HTTP
aiohttp>=3.9.0
websockets>=12.0

# Security
cryptography>=42.0.0

# Configuration
pyyaml>=6.0.1

# Telegram
python-telegram-bot>=20.7

# Testing
pytest>=7.4.0
pytest-asyncio>=0.23.0
pytest-cov>=4.1.0
httpx>=0.26.0

# Utilities
python-dateutil>=2.8.2
structlog>=24.1.0
```

---

## Critical Implementation Notes

### 1. Leg Failure Handling (executor.py)
If second leg fails after first leg fills, MUST immediately market close first leg. This is non-negotiable for risk management.

```python
if second_result is None or second_result.status != "filled":
    await self._emergency_close(first_exchange, symbol, first_side, first_size)
    return None, None
```

### 2. Circuit Breaker (base.py)
After 5 consecutive API failures, pause operations on that exchange and send CRITICAL alert.

```python
if self._consecutive_failures >= 5:
    raise CircuitBreakerOpenError(f"{self.name} circuit breaker open")
```

### 3. Kill Switch (risk_manager.py)
Close ALL positions, cancel ALL orders, halt automation, require manual restart.

### 4. Checkpoint Recovery (main.py)
On startup:
1. Load checkpoint from database
2. Query exchanges for actual positions
3. Reconcile local state with exchange state
4. HALT if mismatch detected (require manual review)

### 5. Hot Reload
Config changes via API apply immediately EXCEPT:
- Exchange credentials (require restart)
- Database connection (require restart)

---

## Execution Order

1. **Foundation** - Config, encryption, logging, requirements.txt
2. **Database** - Models, repository, Alembic migrations
3. **Exchanges** - Base adapter, Binance, Bybit with ccxt
4. **Engine** - Scanner, detector, executor, position manager, risk manager
5. **Alerts** - Telegram integration
6. **API** - FastAPI server, routes, WebSocket
7. **Dashboard** - Update index.html for real API
8. **Tests** - Unit and integration tests
9. **Production** - Docker, scripts, hardening
