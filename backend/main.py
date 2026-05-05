from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from app.routers import market, trade, ai

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


@app.get("/health")
def health():
    return {"status": "ok"}
