from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import os

from app.routers import market, trade, ai, etf, auto_trade
from app.services.market_data import cleanup_all_caches, get_spot_data

app = FastAPI(title="StockSimulator", version="0.1.0")

# CORS：开发环境允许所有，生产环境通过环境变量限定
origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(trade.router, prefix="/api/trade", tags=["trade"])
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
app.include_router(etf.router, prefix="/api/etf", tags=["etf"])
app.include_router(auto_trade.router, prefix="/api/auto-trade", tags=["auto-trade"])


@app.on_event("startup")
async def _start_cache_cleanup():
    async def _loop():
        while True:
            await asyncio.sleep(60)
            cleanup_all_caches()
    asyncio.create_task(_loop())
    # 行情缓存预热，避免首次请求等待
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_spot_data)
    # 启动自动交易调度器
    from app.services.auto_trader import start_scheduler
    await start_scheduler()


@app.get("/health")
def health():
    return {"status": "ok"}
