# Crypto Funding Rate Arbitrage Dashboard

## Project Overview

A single-page dashboard for tracking crypto funding rate arbitrage strategies. Built with vanilla HTML, CSS, and JavaScript with no build tools required. Features a Bloomberg Terminal-inspired design for professional traders.

## Tech Stack

- HTML5 + CSS3 + Vanilla JavaScript
- Chart.js (CDN) for historical charts
- IBM Plex Mono font (Google Fonts)
- Mock/static data

## Features

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
- Input: position size, leverage, long/short exchange rates
- Output: rate spread, per-funding profit, daily/weekly/monthly profit, APR
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

## File Structure

```
crypto-funding-arbitrage/
├── index.html    # Single file with inline CSS and JS
└── CLAUDE.md     # This file
```

## Development

```bash
# Activate virtualenv
source .venv/bin/activate

# Run tests
python -m pytest tests/ -v --tb=short
```

**Important:** Always run the tests after every fix.

## Usage

```bash
open index.html
```

Or serve with any static file server.

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
