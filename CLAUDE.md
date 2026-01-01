# Crypto Funding Rate Arbitrage System

## Overview

A fully autonomous trading system that monitors funding rates across cryptocurrency perpetual futures exchanges, identifies arbitrage opportunities, and executes hedged positions. Features a Bloomberg Terminal-inspired dashboard.

**Core Strategy**: SHORT on exchanges with higher funding rates, LONG on exchanges with lower rates, collecting the spread at each funding interval.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.10+, FastAPI, asyncio |
| Exchange Connectivity | ccxt (Binance, Bybit) |
| Database | SQLite (dev) / PostgreSQL (prod), SQLAlchemy |
| Dashboard | HTML5/CSS3/Vanilla JS, Chart.js |
| Real-time | WebSocket |
| Alerts | Telegram Bot API |
| Configuration | YAML with Fernet encryption |

## Development

```bash
# Activate virtualenv
source .venv/bin/activate

# Run tests
python -m pytest tests/ -v --tb=short

# Start backend server
python -m backend.main

# Or use uvicorn directly
uvicorn backend.api.server:app --reload --port 8000
```

**Important:** Always run tests after every fix.

## File Structure

```
fundingarb/
├── backend/
│   ├── main.py                 # Entry point with graceful shutdown
│   ├── config/
│   │   ├── loader.py           # Config loading with decryption
│   │   └── schema.py           # Pydantic config models
│   ├── exchanges/
│   │   ├── base.py             # Abstract adapter with circuit breaker
│   │   ├── binance.py          # Binance USDT-M futures
│   │   ├── bybit.py            # Bybit linear perpetuals
│   │   ├── factory.py          # Exchange factory
│   │   └── types.py            # FundingRate, Order, Position types
│   ├── engine/
│   │   ├── scanner.py          # Event-driven funding rate scanner
│   │   ├── detector.py         # Arbitrage opportunity detection
│   │   ├── executor.py         # Order execution with leg ordering
│   │   ├── position_manager.py # Position lifecycle management
│   │   ├── risk_manager.py     # Kill switch, liquidation handling
│   │   └── coordinator.py      # Main engine coordinator
│   ├── api/
│   │   ├── server.py           # FastAPI app with lifespan
│   │   ├── websocket.py        # WebSocket manager
│   │   ├── schemas.py          # Pydantic response models
│   │   └── routes/             # API route handlers
│   ├── alerts/
│   │   └── telegram.py         # Telegram notifications
│   ├── database/
│   │   ├── models.py           # Position, Trade, FundingEvent
│   │   ├── connection.py       # Async session management
│   │   └── repository.py       # Data access layer
│   └── utils/
│       ├── encryption.py       # Fernet encryption utilities
│       └── logging.py          # Structured logging
├── dashboard/
│   └── index.html              # Bloomberg-style dashboard
├── config/
│   ├── config.example.yaml     # Example configuration
│   └── config.yaml             # User config (gitignored)
├── tests/
│   ├── unit/                   # Unit tests
│   └── integration/            # Integration tests
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── scripts/
│   └── encrypt_config.py       # Config encryption CLI
├── requirements.txt
├── SPEC.md                     # Full specification
└── PLAN.md                     # Implementation plan
```

## API Endpoints

```
GET  /api/health              # Health check
GET  /api/status              # Engine status
GET  /api/positions           # List positions
GET  /api/funding-rates       # Live funding rates
POST /api/positions/open      # Open hedged position
POST /api/positions/{id}/close # Close position
POST /api/engine/start        # Start automation
POST /api/engine/stop         # Stop automation
POST /api/engine/kill         # Emergency kill switch
POST /api/config/update       # Hot reload config
```

## WebSocket Events

```javascript
{ type: "FUNDING_RATE_UPDATE", data: { exchange, pair, rate, predicted, next_funding_time } }
{ type: "POSITION_UPDATE", data: { position_id, status, unrealized_pnl } }
{ type: "TRADE_EXECUTED", data: { position_id, exchange, side, price, size, fee } }
{ type: "ENGINE_STATUS", data: { status, connected_exchanges, last_scan } }
{ type: "ALERT", data: { severity, message, timestamp } }
```

## Key Trading Logic

**Daily Spread Threshold** (dynamic, normalized to 24h):
```
daily_threshold = 0.03% + (0.003% * position_size_usd / 10,000)
```

All rate comparisons are normalized to daily basis to correctly compare exchanges with different funding intervals (e.g., Binance 8h vs dYdX 1h):
```python
daily_rate = per_interval_rate * (24 / interval_hours)
```

**Execution Order**:
1. Execute lower liquidity exchange first
2. If first leg fills, execute second leg
3. If second leg fails, immediately market close first leg

**Circuit Breaker**: 5 consecutive API failures pause exchange operations

## Dashboard Features

### 1. P&L Summary Cards
- Unrealized P&L - sum of open position gains/losses
- Realized P&L - total from closed positions
- Funding Collected - cumulative funding payments
- Net P&L - overall performance

### 2. Live Funding Rates Table
- Exchanges: Binance, Bybit, OKX, dYdX
- Pairs: BTC, ETH, SOL
- Current and predicted rates
- Annualized APR calculation
- Countdown timer to next funding period (blinks when < 5 min)

### 3. Arbitrage Calculator
- Input: position size, leverage, long/short exchange rates, **funding intervals**
- Output: **daily spread**, daily/weekly/monthly profit, APR
- Correctly normalizes rates across different funding intervals (8h vs 1h)
- Auto-updates on input change

### 4. Position Tracker
- Active positions with entry date, size, exchanges
- Per-position funding collected and unrealized P&L
- Close button to simulate closing positions

### 5. Closed Positions History
- Duration, funding collected, realized P&L

### 6. Historical Funding Rate Charts
- 7-day funding rate history
- Toggle between BTC, ETH, SOL, ARB
- Compare rates across Binance, Bybit, dYdX

### 7. Bloomberg Terminal UI Elements
- Top menu bar with navigation tabs
- Scrolling ticker tape with live prices
- Command line input (type BTC, ETH, SOL, ARB + Enter to switch charts)
- Function key bar (F1-F10)
- Live clock with timezone indicator
- Numbered panel system

## Design

Bloomberg Terminal aesthetic:

- **Colors**
  - Background: Pure black (#000000)
  - Primary accent: Bloomberg orange (#ff6600)
  - Text: Amber (#ffcc00), white, gray hierarchy
  - Positive: Green (#00dd00)
  - Negative: Red (#ff3333)
  - Neutral/info: Blue (#00aaff)

- **Typography**
  - IBM Plex Mono throughout (monospace terminal style)
  - Dense, compact text (11-12px base)
  - Uppercase labels and headers

- **Layout**
  - 2-column grid with 1px borders
  - Numbered panels (1-6)
  - Minimal padding, maximum information density
  - Panel headers with action buttons ([EXPORT], [SORT], etc.)

- **Interactive Elements**
  - Exchange tags with brand colors (BIN, BYB, OKX, DYD)
  - Command line with `>` prompt and `<GO>` button
  - Function key hints (F1-F10)
  - Blinking countdown for urgent states
  - Scrolling ticker tape

## Implementation Notes

### Daily Rate Normalization

All funding rate calculations are normalized to a **daily basis** to correctly compare and rank opportunities across exchanges with different funding intervals.

**Key files**:
- `backend/exchanges/types.py` - `FundingRate.daily_rate` property
- `backend/engine/detector.py` - `ArbitrageOpportunity` with interval-aware fields
- `backend/config/schema.py` - `min_daily_spread_base` configuration
- `dashboard/index.html` - Calculator with interval inputs

**Formula**:
```python
daily_rate = per_interval_rate * (24 / interval_hours)
daily_spread = short_daily_rate - long_daily_rate
```

**Example**:
- Binance BTC: 0.01% per 8h = 0.03% daily (3 payments/day)
- dYdX BTC: -0.005% per 1h = -0.12% daily (24 payments/day)
- Daily spread: 0.03% - (-0.12%) = 0.15% daily
