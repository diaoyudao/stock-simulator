# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股低价股模拟炒股 — Chinese A-share low-price stock trading simulator. Screens stocks under ¥5, shows real-time market data from Sina Finance API, and simulates trading with a virtual ¥100,000 account.

## Commands

```bash
# Backend (FastAPI, runs on :8000)
cd backend
source .venv/Scripts/activate   # Windows; Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend (Vite + React, runs on :5173)
cd frontend
npm install
npm run dev        # dev server with HMR
npm run build      # tsc + vite build
npm run lint       # ESLint

# Bootstrap everything
bash init.sh
```

No test suite exists yet.

## Architecture

```
Browser → Vite :5173 ──proxy /api──→ FastAPI :8000 → Sina/Tencent APIs
                                         │
                                         ▼
                                    SQLite (backend/data/stock_sim.db)
```

### Backend (`backend/`)

- **Entry**: `main.py` — FastAPI app, CORS enabled, two routers
- **Routers**: `app/routers/market.py` (5 endpoints) and `app/routers/trade.py` (6 endpoints)
- **Services**:
  - `app/services/market_data.py` — Data fetching, caching, and the two-round filtering strategy
  - `app/services/trading.py` — Account management, buy/sell, SQLite persistence via aiosqlite

### Frontend (`frontend/`)

- Single-file React app: `src/App.tsx` (~540 lines) with `src/api.ts` as the API client
- No React Router — navigation via `selectedStock` state (detail view replaces main view)
- No state management library — prop drilling and `useCallback`/`useState`
- K-line chart via TradingView `lightweight-charts`
- Vite proxies `/api` to `http://127.0.0.1:8000` (see `vite.config.ts`)

## Key Design Decisions

**Two-round filtering** (`market_data.py:filter_low_price`):
1. Basic filters (price, change%, PE, PB, etc.) applied to Sina cached data → `pre_filtered`
2. Tencent API enrichment (volume ratio, 52-week high/low) only for `pre_filtered` (max 500 stocks)
3. Volume ratio / 52-week filters applied to enriched results

This avoids calling Tencent API on all 5000+ stocks, which causes 120s+ timeouts.

**Data sources**:
- Sina Finance API: primary source for all market data (paginated, 80/page, ~80 pages)
- Tencent API: supplementary for volume ratio + 52-week high/low only
- Sector mapping: Sina industry hierarchy (48 sectors), built from top-30 industry pages

**Caching**:
- Market data: in-memory, 60s TTL (cold fetch ~60s due to Sina pagination)
- Sector list: 5 min TTL
- Sector mapping: 5 min TTL (~10s to build)
- K-line: not cached, fetched on demand

**Monthly K-line**: Sina doesn't support monthly scale directly; `_aggregate_monthly()` aggregates 250 daily bars into monthly.

## Database Schema

SQLite tables created inline in `trading.py:_ensure_tables()` — no migration system.

- `account` (id=1, cash REAL, default 100000)
- `positions` (code PK, name, quantity, avg_cost)
- `transactions` (id PK, code, name, action 'buy'|'sell', quantity, price, amount, created_at unix timestamp)

## Conventions

- Chinese keys in API responses (e.g., `"代码"`, `"涨跌幅"`, `"最新价"`) — Sina API field names preserved as-is
- A-share color convention: **red = up, green = down** (opposite of US markets)
- Trade quantities must be multiples of 100 (A-share lot size)
- `akshare` is listed in `requirements.txt` but unused — project switched to Sina Finance API
