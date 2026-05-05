from fastapi import APIRouter

from app.services.ai_analysis import get_ai_analysis, get_ai_score
from app.utils import validate_code

router = APIRouter()


@router.get("/analyze/{code}")
async def analyze(code: str):
    """LLM驱动的综合分析。"""
    validate_code(code)
    return await get_ai_analysis(code)


@router.get("/score/{code}")
async def score(code: str):
    """规则评分引擎，即时返回。"""
    validate_code(code)
    return await get_ai_score(code)
