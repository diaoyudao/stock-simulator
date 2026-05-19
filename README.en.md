# A-Stock Low-Price Simulator

Filter A-shares under ¥5, display real-time quotes, simulate trading with ¥100K virtual funds.

## Features

- **Stock Screening** — Multi-dimension filters: price, change%, turnover, PE, PB, volume ratio, Shenwan industry
- **Real-time Quotes** — Multi-source: Sina Finance + Tencent API + Eastmoney HTTP, 60s refresh with fallback
- **Paper Trading** — ¥100K virtual funds, A-share lot rules (100 shares), limit orders
- **K-line Charts** — Daily/Weekly/Monthly, MA/BOLL/MACD/KDJ/RSI indicators
- **ETF Quotes** — ETF screening, detail, holdings, NAV, asset allocation
- **Watchlist** — Group management, real-time price tracking
- **Industry Sectors** — Shenwan classification (31 L1 + 129 L2), THS fund flow data, main force net + strength analysis
- **Performance** — Equity curve, return stats, benchmark comparison
- **Price Alerts** — Auto-notify on price targets, 30s polling
- **AI Analysis** — LLM comprehensive analysis + rule-based scoring (rate-limited)
- **Mobile Ready** — Responsive layout for phone and tablet

## Tech Stack

| Layer | Tech |
|---|------|
| Frontend | React 19 + TypeScript + Vite + TradingView Lightweight Charts |
| Backend | FastAPI + aiosqlite + requests |
| Data Sources | astock_data + mootdx(TCP) + Tencent HTTP + Baidu HTTP + Eastmoney HTTP + Sina HTTP + AKShare(fallback) |
| Database | SQLite |

## Quick Start

```bash
# One-click start (recommended)
bash dev.sh start          # Start frontend & backend
bash dev.sh stop           # Stop
bash dev.sh restart        # Restart

# Or manual start
cd backend && source .venv/Scripts/activate && uvicorn main:app --reload
cd frontend && npm install && npm run dev
```

Visit http://localhost:5173

## Project Structure

```
backend/
  main.py                       # FastAPI entry
  app/routers/market.py         # Market endpoints (15)
  app/routers/trade.py          # Trade endpoints (24)
  app/routers/etf.py            # ETF endpoints (8)
  app/routers/ai.py             # AI analysis endpoints (2)
  app/services/market_data.py   # Data fetching + caching + two-round filtering
  app/services/trading.py       # Trading logic + SQLite persistence
  app/services/ai_analysis.py   # LLM analysis + rule scoring engine
  app/services/astock_data.py   # Data source layer (a-stock-data)
frontend/
  src/App.tsx                   # Main app
  src/components/StockDetail.tsx # Detail page
  src/api.ts                    # API client (with cache)
  src/utils/indicators.ts       # Technical indicator calculations
```

## Performance

- Two-round filtering: basic filter → Tencent volume-ratio/52w only for pre-filtered stocks
- Background cache warm-up, zero cold-start for users
- Fund flow file cache for historical data (datacenter returns current day only)
- O(1) stock code index + price map cache
- Frontend 400ms debounce + 30s in-memory GET cache

## License

MIT
