from fastapi import APIRouter, Request, HTTPException

from app.services.ai_analysis import get_ai_analysis, get_ai_score
from app.rate_limiter import RateLimiter
from app.utils import validate_code

router = APIRouter()

_analyze_limiter = RateLimiter(max_requests=10, window_seconds=60)


@router.get("/analyze/{code}")
async def analyze(code: str, request: Request):
    """LLM驱动的综合分析。"""
    validate_code(code)
    client_ip = request.client.host if request.client else "unknown"
    if not _analyze_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    return await get_ai_analysis(code)


@router.get("/score/{code}")
async def score(code: str):
    """规则评分引擎，即时返回。"""
    validate_code(code)
    return await get_ai_score(code)
