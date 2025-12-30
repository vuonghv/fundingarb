# Funding Rate Arbitrage Cross-Exchange Strategy

## Overview

A fully autonomous trading system that monitors funding rates across cryptocurrency perpetual futures exchanges, identifies arbitrage opportunities, and executes hedged positions to capture funding rate differentials.

**Core Strategy**: Open SHORT on exchanges with higher funding rates and LONG on exchanges with lower funding rates for the same asset, collecting the spread at each funding interval.

---

## 1. System Architecture

### 1.1 Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    Bloomberg-Style Dashboard                      │
│              (HTML/CSS/JS - Existing + New Panels)               │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ WebSocket (real-time push)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Trading Engine (Python)                       │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ Rate Scanner│  │ Arb Detector │  │ Execution Engine        │ │
│  │ (event-driv)│  │              │  │ (ccxt + asyncio)        │ │
│  └─────────────┘  └──────────────┘  └─────────────────────────┘ │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ Position Mgr│  │ Risk Manager │  │ Alert Service (Telegram)│ │
│  └─────────────┘  └──────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Database (Configurable)                        │
│                    SQLite (dev) / PostgreSQL (prod)              │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Tech Stack

| Component | Technology |
|-----------|------------|
| Trading Backend | Python 3.10+ with ccxt, asyncio |
| Exchange Connectivity | ccxt library (unified API) |
| Database | Configurable: SQLite or PostgreSQL |
| Dashboard | Existing HTML/CSS/JS (extend with new panels) |
| Real-time Communication | WebSocket (backend → dashboard) |
| Alerts | Telegram Bot API |
| Configuration | Encrypted JSON/YAML file |
| Data Feeds | Exchange-specific optimal (WebSocket primary, REST fallback) |

---

## 2. Exchange Integration

### 2.1 Pluggable Architecture

The system uses a pluggable exchange adapter pattern allowing easy addition of new exchanges.

```python
class ExchangeAdapter(ABC):
    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> FundingRate
    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int) -> OrderBook
    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult
    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool
    @abstractmethod
    async def get_positions(self) -> List[Position]
    @abstractmethod
    async def get_fee_tier(self) -> FeeTier
```

### 2.2 Supported Contract Types

- **Linear USDT-margined perpetuals only**
- No inverse contracts, no quanto contracts
- Simplifies position sizing and P&L calculations

### 2.3 Data Feed Strategy

| Exchange | Primary Feed | Fallback |
|----------|-------------|----------|
| Binance | WebSocket | REST |
| Bybit | WebSocket | REST |
| OKX | WebSocket | REST |
| dYdX | WebSocket | REST |

### 2.4 API Rate Limit Handling

- Strictly respect exchange rate limits
- Queue requests when approaching limits
- Prioritize critical operations (position open/close) over data fetching
- Circuit breaker: After 5 consecutive API failures, pause operations on that exchange and alert

---

## 3. Trading Logic

### 3.1 Opportunity Detection

**Scanning**: Event-driven — scan for opportunities only when funding rate updates are received.

**Minimum Spread Threshold** (dynamic, based on position size):
```
threshold = 0.01% + (0.001% × (position_size_usd / 10,000))

Examples:
- $10,000 position: 0.01% + 0.001% = 0.011%
- $50,000 position: 0.01% + 0.005% = 0.015%
- $100,000 position: 0.01% + 0.01% = 0.02%
```

**Fee Calculation**: Query actual fee tier from exchange API (accounts for VIP discounts).

**Opportunity Prioritization**: When multiple opportunities exist, execute highest spread first.

### 3.2 Entry Execution

**Timing**: Open positions 15-30 minutes before funding timestamp to allow buffer for execution.

**Execution Order**:
1. Execute on **lower liquidity exchange first** (more likely to fail/slip)
2. If first leg fills successfully, execute second leg
3. If second leg fails → **immediately close first leg** at market price

**Order Type**: Limit orders at mid-price, cancel if unfilled within user-configurable timeout.

**Position Sizing**: Capped by `min(exchange_A_capacity, exchange_B_capacity)` to ensure both legs can be filled.

### 3.3 Position Management

**Concurrent Positions**: Maximum 1 hedged position per trading pair at a time.

**Position Tracking**: Per exchange-pair combination (Binance-BTC and Bybit-BTC are tracked separately).

**Holding Strategy**:
- Hold until next funding if spread remains profitable
- Collect multiple funding payments when conditions remain favorable

### 3.4 Exit Execution

**Closing Orders**: Simultaneous fire-and-forget (send both close orders at once).

**Exit Triggers**:
1. Spread becomes significantly negative (configurable tolerance per position)
2. One leg gets liquidated → immediately market close surviving leg
3. Manual close via dashboard
4. Kill switch activated

**Spread Inversion Handling**:
- When funding rate crosses zero mid-position, evaluate based on spread magnitude
- Small crossings tolerated; significant inversions trigger close

### 3.5 Negative Spread Tolerance

Configurable per position. Example settings:
- Conservative: Close immediately on negative spread
- Moderate: Tolerate up to -0.01% spread
- Aggressive: Hold through minor inversions

---

## 4. Risk Management

### 4.1 Exposure Limits

- **Per-pair position limit**: Maximum notional USD per trading pair
- User configures limit in settings (e.g., max $50,000 per pair)

### 4.2 Leverage

- User-configurable per exchange and per pair
- Stored in config, adjustable via dashboard
- Example config:
```json
{
  "leverage": {
    "binance": {"default": 5, "BTC-USDT": 3, "ETH-USDT": 5},
    "bybit": {"default": 5}
  }
}
```

### 4.3 Liquidation Handling

If one leg of a hedged position gets liquidated:
1. Detect liquidation event via WebSocket or position polling
2. Immediately market close the surviving leg
3. Log the event with full details
4. Send CRITICAL alert via Telegram
5. Pause new entries on that pair for configurable cooldown period

### 4.4 Kill Switch

**Full Kill Switch** (accessible via dashboard and Telegram command):
1. Cancel all pending orders
2. Market close all open positions on all exchanges
3. Halt all automation
4. Send CRITICAL alert
5. Require manual restart to resume trading

### 4.5 Exchange Downtime Handling

When an exchange becomes unavailable:
1. Pause all trading on affected exchange
2. Keep existing positions open (do not attempt to close)
3. Send WARNING alert
4. Resume operations when connectivity restored
5. Reconcile state with exchange after reconnection

---

## 5. State Management

### 5.1 Database Schema

**Positions Table**:
```sql
CREATE TABLE positions (
    id UUID PRIMARY KEY,
    pair VARCHAR(20) NOT NULL,
    long_exchange VARCHAR(20) NOT NULL,
    short_exchange VARCHAR(20) NOT NULL,
    long_entry_price DECIMAL(20, 8),
    short_entry_price DECIMAL(20, 8),
    size_usd DECIMAL(20, 2),
    leverage_long INTEGER,
    leverage_short INTEGER,
    entry_timestamp TIMESTAMP,
    entry_funding_spread DECIMAL(10, 6),
    status VARCHAR(20), -- OPEN, CLOSED, LIQUIDATED
    close_timestamp TIMESTAMP,
    realized_pnl DECIMAL(20, 2),
    funding_collected DECIMAL(20, 2)
);
```

**Trades Table**:
```sql
CREATE TABLE trades (
    id UUID PRIMARY KEY,
    position_id UUID REFERENCES positions(id),
    exchange VARCHAR(20),
    side VARCHAR(10), -- LONG, SHORT
    action VARCHAR(10), -- OPEN, CLOSE
    order_type VARCHAR(10),
    price DECIMAL(20, 8),
    size DECIMAL(20, 8),
    fee DECIMAL(20, 8),
    timestamp TIMESTAMP,
    order_id VARCHAR(100),
    status VARCHAR(20)
);
```

**Funding Events Table**:
```sql
CREATE TABLE funding_events (
    id UUID PRIMARY KEY,
    position_id UUID REFERENCES positions(id),
    exchange VARCHAR(20),
    pair VARCHAR(20),
    funding_rate DECIMAL(10, 8),
    payment_usd DECIMAL(20, 8),
    timestamp TIMESTAMP
);
```

### 5.2 Checkpoint and Recovery

**Checkpointing**:
- Persist state to database after every state change
- Include: open positions, pending orders, configuration snapshot

**Recovery on Restart**:
1. Load checkpoint from database
2. Query all exchanges for current positions
3. Reconcile local state with exchange state
4. Resume automation if states match
5. If mismatch detected → halt and alert for manual review

---

## 6. Configuration

### 6.1 Credentials Storage

- Encrypted JSON/YAML config file
- Master password required to decrypt on startup
- Never log or expose API secrets

```yaml
# config.encrypted.yaml (decrypted structure)
exchanges:
  binance:
    api_key: "xxx"
    api_secret: "xxx"
    testnet: false
  bybit:
    api_key: "xxx"
    api_secret: "xxx"
    testnet: false
```

### 6.2 Trading Configuration

```yaml
trading:
  symbols:
    - "BTC-USDT"
    - "ETH-USDT"
    - "SOL-USDT"

  min_spread_base: 0.0001  # 0.01%
  min_spread_per_10k: 0.00001  # 0.001% per $10k

  entry_buffer_minutes: 20  # Enter 20 min before funding

  order_fill_timeout_seconds: 30  # User-configurable

  max_position_per_pair_usd: 50000

  negative_spread_tolerance: -0.0001  # Close if spread < -0.01%

  leverage:
    default: 5
    BTC-USDT: 3
    ETH-USDT: 5
```

### 6.3 Hot Reload

Configuration changes applied via dashboard API without restart:
- Trading pairs
- Leverage settings
- Spread thresholds
- Alert preferences

**Require restart for**:
- Exchange credentials
- Database connection

---

## 7. Alerting System

### 7.1 Channel

**Telegram Bot** — single channel for all alerts.

### 7.2 Severity Levels

| Level | Events | Action |
|-------|--------|--------|
| INFO | New position opened, position closed, funding collected | Log + Telegram |
| WARNING | Spread widening, exchange connectivity issues, approaching limits | Log + Telegram |
| CRITICAL | Liquidation, second leg failure, kill switch, system error | Log + Telegram (with sound/priority) |

### 7.3 Alert Format

```
🟢 INFO | Position Opened
Pair: BTC-USDT
Long: Binance @ $42,150.00
Short: Bybit @ $42,145.00
Size: $25,000 | Spread: 0.032%
Expected funding: $8.00

🔴 CRITICAL | Liquidation Detected
Pair: ETH-USDT
Liquidated: SHORT on OKX
Surviving leg closed @ market
Loss: -$1,234.56
Action: Pausing ETH-USDT for 1 hour
```

---

## 8. Dashboard Integration

### 8.1 Existing Panels (Keep)

1. P&L Summary Cards
2. Live Funding Rates Table
3. Arbitrage Calculator
4. Position Tracker
5. Closed Positions History
6. Historical Funding Rate Charts
7. Bloomberg UI elements (ticker, command bar, function keys)

### 8.2 New Trading Control Panels

**Panel 7: Trading Engine Status**
- Engine status: Running / Paused / Error
- Connected exchanges (green/red indicators)
- Last scan timestamp
- Pending orders count

**Panel 8: Quick Actions**
- Start/Stop automation toggle
- Kill Switch button (requires confirmation)
- Force scan button
- Refresh all data button

**Panel 9: Position Entry Form**
- Symbol selector (from configured pairs)
- Long exchange dropdown
- Short exchange dropdown
- Position size input (USD)
- Calculated spread display
- [OPEN POSITION] button

**Panel 10: Configuration Editor**
- Symbols whitelist (add/remove)
- Leverage sliders per exchange
- Spread threshold inputs
- Timeout settings
- [SAVE & APPLY] button (hot reload)

### 8.3 WebSocket Events

Backend → Dashboard:
```javascript
// Event types
{
  type: "POSITION_UPDATE",
  data: { position_id, status, unrealized_pnl, ... }
}
{
  type: "FUNDING_RATE_UPDATE",
  data: { exchange, pair, rate, predicted, next_funding_time }
}
{
  type: "TRADE_EXECUTED",
  data: { position_id, exchange, side, price, size, fee }
}
{
  type: "ENGINE_STATUS",
  data: { status, connected_exchanges, last_scan, error }
}
{
  type: "ALERT",
  data: { severity, message, timestamp }
}
```

Dashboard → Backend (via REST API):
```
POST /api/positions/open
POST /api/positions/{id}/close
POST /api/engine/start
POST /api/engine/stop
POST /api/engine/kill
POST /api/config/update
GET  /api/status
GET  /api/positions
GET  /api/history
```

---

## 9. Simulation Mode

### 9.1 Requirement

**Mandatory simulation mode** before live trading for new users/configurations.

### 9.2 Implementation

- Uses exchange testnet APIs (Binance testnet, Bybit testnet, etc.)
- Real order execution on testnet
- Real funding rate data from mainnet (testnets may have different rates)
- Full logging as if live
- Dashboard shows [SIMULATION] badge when active

### 9.3 Transition to Live

1. Run simulation for minimum N hours (configurable, default 24h)
2. Review simulation results in dashboard
3. Manually toggle to live mode via dashboard
4. Confirm with master password

---

## 10. Logging and Metrics

### 10.1 Logging Level

**Standard**: Trades, orders, funding events, errors.

Logged fields per trade:
- Timestamp
- Position ID
- Exchange
- Pair
- Side
- Action (open/close)
- Order type
- Price
- Size
- Fee
- Order ID
- Status
- Latency (ms)

### 10.2 Performance Metrics

Basic metrics tracked:
- Total P&L (realized)
- Total P&L (unrealized)
- Total funding collected
- Win rate (profitable positions / total positions)
- Trade count
- Average position duration

### 10.3 Log Retention

- Trade logs: 1 year
- Error logs: 90 days
- Debug logs: 7 days (when enabled)

---

## 11. Security Considerations

### 11.1 API Key Permissions

Recommended exchange API key permissions:
- ✅ Read account balance
- ✅ Read positions
- ✅ Read orders
- ✅ Place orders
- ✅ Cancel orders
- ❌ Withdraw (never enable)
- ❌ Transfer (never enable)

### 11.2 No IP Whitelisting

System does not manage IP whitelisting. User is responsible for:
- Configuring IP restrictions on exchange if desired
- Running system on stable IP if using IP whitelist
- Updating whitelist if IP changes

### 11.3 Credential Security

- Master password encrypts config at rest
- Credentials never logged
- Memory cleared after use where possible
- No credentials in environment variables (encrypted file only)

---

## 12. Deployment

### 12.1 Recommended Setup

- **VPS** with stable internet (AWS, GCP, DigitalOcean)
- **16GB RAM**, 4 vCPU minimum
- **SSD storage** for database
- **Static IP** if using exchange IP whitelist

### 12.2 Process Management

- Use systemd or supervisor for process management
- Auto-restart on crash
- Checkpoint recovery ensures no position loss

### 12.3 Monitoring

- Health check endpoint: `GET /api/health`
- Process monitor alerts if backend becomes unresponsive
- Database backup daily

---

## 13. File Structure

```
fundingarb/
├── backend/
│   ├── main.py                 # Entry point
│   ├── config/
│   │   ├── loader.py           # Config loading and decryption
│   │   └── schema.py           # Config validation
│   ├── exchanges/
│   │   ├── base.py             # Abstract exchange adapter
│   │   ├── binance.py
│   │   ├── bybit.py
│   │   ├── okx.py
│   │   └── dydx.py
│   ├── engine/
│   │   ├── scanner.py          # Funding rate scanner
│   │   ├── detector.py         # Arbitrage opportunity detector
│   │   ├── executor.py         # Order execution engine
│   │   ├── position_manager.py # Position lifecycle management
│   │   └── risk_manager.py     # Risk controls and limits
│   ├── api/
│   │   ├── server.py           # REST API server
│   │   ├── websocket.py        # WebSocket server
│   │   └── routes.py           # API route handlers
│   ├── alerts/
│   │   └── telegram.py         # Telegram bot integration
│   ├── database/
│   │   ├── models.py           # SQLAlchemy models
│   │   ├── repository.py       # Database operations
│   │   └── migrations/         # Alembic migrations
│   └── utils/
│       ├── encryption.py       # Config encryption/decryption
│       └── logging.py          # Logging setup
├── dashboard/
│   └── index.html              # Extended Bloomberg-style dashboard
├── config/
│   ├── config.example.yaml     # Example configuration
│   └── config.encrypted.yaml   # Encrypted user config
├── tests/
│   ├── test_exchanges/
│   ├── test_engine/
│   └── test_integration/
├── requirements.txt
├── SPEC.md                     # This file
├── CLAUDE.md                   # AI assistant instructions
└── README.md                   # User documentation
```

---

## 14. Success Criteria

### MVP Requirements

1. ✅ Connect to at least 2 exchanges (Binance + Bybit)
2. ✅ Monitor funding rates in real-time
3. ✅ Detect arbitrage opportunities based on spread threshold
4. ✅ Execute hedged positions automatically
5. ✅ Collect funding payments
6. ✅ Handle second-leg failures gracefully
7. ✅ Telegram alerts for key events
8. ✅ Dashboard shows live positions and P&L
9. ✅ Manual position control via dashboard
10. ✅ Simulation mode with testnet

### Post-MVP Enhancements

- Additional exchanges (OKX, dYdX, etc.)
- Advanced metrics and analytics
- Multi-account support
- Mobile-responsive dashboard
- Historical backtesting
- Machine learning for spread prediction
