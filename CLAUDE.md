# Crypto Funding Rate Arbitrage Dashboard

## Project Overview

A single-page dashboard for tracking crypto funding rate arbitrage strategies. Built with vanilla HTML, CSS, and JavaScript with no build tools required.

## Tech Stack

- HTML5 + CSS3 + Vanilla JavaScript
- Chart.js (CDN) for historical charts
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
- Countdown timer to next funding period

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

## File Structure

```
crypto-funding-arbitrage/
├── index.html    # Single file with inline CSS and JS
└── CLAUDE.md     # This file
```

## Usage

```bash
open index.html
```

Or serve with any static file server.

## Design

- Dark theme (#0d1117 background)
- Green (#3fb950) for positive values
- Red (#f85149) for negative values
- Blue (#58a6ff) for accents
- Exchange-specific badge colors
- Responsive layout for mobile
- Monospace fonts for numbers
