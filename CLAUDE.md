# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指引。

## 项目概述

A股低价股模拟炒股 — 筛选5元以下A股，展示新浪财经实时行情，10万虚拟资金模拟交易。

## 常用命令

```bash
# 后端（FastAPI，端口 :8000）
cd backend
source .venv/Scripts/activate   # Windows；Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload

# 前端（Vite + React，端口 :5173）
cd frontend
npm install
npm run dev        # 开发服务器（HMR）
npm run build      # tsc + vite 构建
npm run lint       # ESLint 检查

# 一键初始化
bash init.sh
```

暂无测试套件。

## 架构

```
浏览器 → Vite :5173 ──代理 /api──→ FastAPI :8000 → 新浪/腾讯 API
                                         │
                                         ▼
                                    SQLite (backend/data/stock_sim.db)
```

### 后端（`backend/`）

- **入口**：`main.py` — FastAPI 应用，CORS 已开启，两个路由模块
- **路由**：`app/routers/market.py`（5个端点）和 `app/routers/trade.py`（6个端点）
- **服务层**：
  - `app/services/market_data.py` — 数据获取、缓存、两轮筛选策略
  - `app/services/trading.py` — 账户管理、买卖交易、SQLite 持久化（aiosqlite）

### 前端（`frontend/`）

- 单文件 React 应用：`src/App.tsx`（~540行），`src/api.ts` 为 API 客户端
- 无 React Router — 通过 `selectedStock` 状态切换详情页
- 无状态管理库 — prop 传递 + `useCallback`/`useState`
- K线图使用 TradingView `lightweight-charts`
- Vite 将 `/api` 代理到 `http://127.0.0.1:8000`（见 `vite.config.ts`）

## 核心设计决策

**两轮筛选策略**（`market_data.py:filter_low_price`）：
1. 基础筛选（价格、涨跌幅、市盈率、市净率等）作用于新浪缓存数据 → `pre_filtered`
2. 仅对 `pre_filtered`（最多500只）调用腾讯 API 补充量比、52周高低
3. 在补充数据后再应用量比 / 52周筛选条件

避免对5000+股票全量调用腾讯 API（会导致120秒+超时）。

**数据源**：
- 新浪财经 API：主要数据源，分页获取全市场行情（每页80条，约80页）
- 腾讯 API：补充数据源，仅获取量比 + 52周高低
- 行业映射：新浪行业层级（48个行业），取前30个行业构建代码→行业映射

**缓存策略**：
- 行情数据：内存缓存，60秒 TTL（冷获取约60秒）
- 行业列表：5分钟 TTL
- 行业映射：5分钟 TTL（构建约10秒）
- K线数据：不缓存，按需获取

**月K线**：新浪不支持月K周期，`_aggregate_monthly()` 从250条日K数据聚合。

**交易时间限制**：A股交易时间（周一至周五 9:30-11:30, 13:00-15:00），前后端双重校验，非交易时间禁止交易。

## 数据库结构

SQLite 表在 `trading.py:_ensure_tables()` 中内联创建，无迁移系统。

- `account`（id=1, cash REAL, 默认100000）
- `positions`（code 主键, name, quantity, avg_cost）
- `transactions`（id 主键, code, name, action 'buy'|'sell', quantity, price, amount, created_at unix时间戳）

## 代码约定

- API 响应使用中文 key（如 `"代码"`、`"涨跌幅"`、`"最新价"`）— 保留新浪原始字段名
- A股配色：**红涨绿跌**（与美股相反）
- 交易数量必须为100的整数倍（A股整手规则）
- `akshare` 在 `requirements.txt` 中但未使用 — 项目已切换到新浪财经 API
