"""启动脚本 — 加载 .env 后运行 uvicorn。"""
import os
import sys
from pathlib import Path

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload="--reload" in sys.argv)
