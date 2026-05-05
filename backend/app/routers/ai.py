import re
from fastapi import APIRouter, HTTPException

from app.services.ai_analysis import get_ai_analysis, get_ai_score

router = APIRouter()

_CODE_PATTERN = re.compile(r"^[shsz]?\d{6}$")


def _validate_code(code: str) -> None:
    if not _CODE_PATTERN.match(code):
        raise HTTPException(status_code=422, detail=f"无效的股票代码: {code}")


@router.get("/analyze/{code}")
async def analyze(code: str):
    """LLM驱动的综合分析。"""
    _validate_code(code)
    return await get_ai_analysis(code)


@router.get("/score/{code}")
async def score(code: str):
    """规则评分引擎，即时返回。"""
    _validate_code(code)
    return get_ai_score(code)
