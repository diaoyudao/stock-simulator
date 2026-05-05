import re
from fastapi import HTTPException

CODE_PATTERN = re.compile(r"^[shsz]?\d{6}$")


def validate_code(code: str) -> None:
    if not CODE_PATTERN.match(code):
        raise HTTPException(status_code=422, detail=f"无效的股票代码: {code}")
