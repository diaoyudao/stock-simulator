from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import market, trade

app = FastAPI(title="StockSimulator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router, prefix="/api/market", tags=["market"])
app.include_router(trade.router, prefix="/api/trade", tags=["trade"])


@app.get("/health")
def health():
    return {"status": "ok"}
