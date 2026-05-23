from fastapi import APIRouter, Request, HTTPException, Query

from app.services.ai_analysis import get_ai_analysis, get_ai_score, screen_stocks
from app.rate_limiter import RateLimiter
from app.utils import validate_code

router = APIRouter()

_analyze_limiter = RateLimiter(max_requests=10, window_seconds=60)
_score_limiter = RateLimiter(max_requests=15, window_seconds=60)
_screen_limiter = RateLimiter(max_requests=5, window_seconds=60)


@router.get("/screen")
async def screen(
    request: Request,
    min_price: float = Query(1.0, ge=0.1, le=100),
    max_price: float = Query(5.0, ge=0.1, le=100),
    top_n: int = Query(30, ge=1, le=100),
    exclude_st: bool = Query(True),
    strategy: str = Query("balanced"),
):
    """多因子智能选股 — 支持策略模式(balanced/oversold_bounce)。"""
    client_ip = request.client.host if request.client else "unknown"
    if not _screen_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="选股请求过于频繁，请稍后再试")
    return await screen_stocks(
        min_price=min_price, max_price=max_price,
        top_n=top_n, exclude_st=exclude_st,
        strategy=strategy,
    )


@router.get("/analyze/{code}")
async def analyze(code: str, request: Request):
    """LLM驱动的综合分析。"""
    validate_code(code)
    client_ip = request.client.host if request.client else "unknown"
    if not _analyze_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    return await get_ai_analysis(code)


@router.get("/score/{code}")
async def score(code: str, request: Request):
    """规则评分引擎，即时返回。"""
    validate_code(code)
    client_ip = request.client.host if request.client else "unknown"
    if not _score_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="评分请求过于频繁，请稍后再试")
    return await get_ai_score(code)
