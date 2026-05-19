# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# OpenWolf

@.wolf/OPENWOLF.md

This project uses OpenWolf for context management. Read and follow .wolf/OPENWOLF.md every session. Check .wolf/cerebrum.md before generating code. Check .wolf/anatomy.md before reading files.

## 项目概述

A股低价股模拟炒股 — 筛选5元以下A股，展示新浪财经实时行情，10万虚拟资金模拟交易。

## 常用命令

```bash
# 一键启停（推荐）
bash dev.sh start          # 启动前后端
bash dev.sh stop           # 停止前后端
bash dev.sh restart        # 重启

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

# 测试（BDD，pytest-bdd）
cd backend && source .venv/Scripts/activate
python -m pytest tests/ -v                          # 运行全部
python -m pytest tests/test_etf_bdd.py -v           # 运行单个
python -m pytest tests/test_market_data.py -v        # 单元测试

# 一键初始化
bash init.sh
```

## 架构

```
浏览器 → Vite :5173 ──代理 /api──→ FastAPI :8000 → 新浪/腾讯/东方财富 API
                                         │
                                         ▼
                                    SQLite (backend/data/stock_sim.db)
```

### 后端（`backend/`）

- **入口**：`main.py` — FastAPI 应用，CORS 已开启，4个路由模块
- **路由**：
  - `app/routers/market.py` — A股行情（15个端点）
  - `app/routers/trade.py` — 交易/账户/自选/委托/提醒（24个端点）
  - `app/routers/etf.py` — ETF行情/持仓/净值（8个端点）
  - `app/routers/ai.py` — AI分析/评分（2个端点，限流保护）
- **服务层**：
  - `app/services/market_data.py`（~2400行）— 数据获取、缓存、两轮筛选策略、分时/盘口/资金流向/分钟K线/财务/资讯/ETF
  - `app/services/trading.py` — 账户管理、买卖交易、SQLite 持久化（aiosqlite）
  - `app/services/ai_analysis.py` — LLM综合分析 + 规则评分引擎
- **工具**：`app/utils.py`（校验等）、`app/rate_limiter.py`（IP级限流）

### 前端（`frontend/`）

- 主应用 `src/App.tsx`（~1180行），详情页已拆分为 `src/components/StockDetail.tsx`（~880行）
- `src/api.ts` — API 客户端（带GET缓存）
- `src/utils/` — `format.ts`（格式化）、`indicators.ts`（技术指标）、`shared.tsx`（共享组件）
- 无 React Router — 通过 `selectedStock` 状态切换详情页
- 无状态管理库 — prop 传递 + `useCallback`/`useState`
- K线图使用 TradingView `lightweight-charts`
- Vite 将 `/api` 代理到 `http://127.0.0.1:8000`（见 `vite.config.ts`）
- 移动端自适应：768px + 480px 双断点，Tab栏固定底部

## 核心设计决策

**两轮筛选策略**（`market_data.py:filter_low_price`）：
1. 基础筛选（价格、涨跌幅、市盈率、市净率等）作用于新浪缓存数据 → `pre_filtered`
2. 仅对 `pre_filtered`（最多500只）调用腾讯 API 补充量比、52周高低
3. 在补充数据后再应用量比 / 52周筛选条件

避免对5000+股票全量调用腾讯 API（会导致120秒+超时）。

**数据源**：
- 新浪财经 API：主要数据源，分页获取全市场行情（每页80条，约80页）
- 腾讯 API：补充数据源，仅获取量比 + 52周高低
- AKShare：分时图(`stock_intraday_em`)、五档盘口(`stock_bid_ask_em`)、资金流向(`stock_individual_fund_flow`)、分钟K线(`stock_zh_a_minute`)、财务摘要(`stock_financial_abstract_ths`)、三大报表(`stock_financial_report_sina`)
- 东方财富搜索API：个股资讯（直接HTTP请求，`stock_news_em` 当前版本broken）
- 行业映射：新浪行业层级（48个行业），取前30个行业构建代码→行业映射

**缓存策略**：
- 行情数据：内存缓存，60秒 TTL，过期前10秒后台预热（用户无冷加载）
- 行业列表：5分钟 TTL
- 行业映射：5分钟 TTL（构建约10秒）
- K线数据：60秒 TTL
- 大盘指数：60秒 TTL
- 分时数据：60秒 TTL
- 五档盘口：10秒 TTL
- 资金流向：5分钟 TTL
- 分钟K线：60秒 TTL
- 财务数据：5分钟 TTL
- 资讯数据：5分钟 TTL
- 股票代码索引：随行情缓存同步更新，O(1)查找
- 价格映射：随行情缓存同步更新，避免重复构建

**月K线**：新浪不支持月K周期，`_aggregate_monthly()` 从250条日K数据聚合。

**交易时间限制**：A股交易时间（周一至周五 9:30-11:30, 13:00-15:00），前后端双重校验，非交易时间禁止交易。

## 数据库结构

SQLite 表在 `trading.py:_ensure_tables()` 中内联创建，无迁移系统。全局共享连接，启动时建表一次。

- `account`（id=1, cash REAL, 默认100000）
- `positions`（code 主键, name, quantity, avg_cost）
- `transactions`（id 主键, code, name, action 'buy'|'sell', quantity, price, amount, created_at unix时间戳）
- `pending_orders`（id 主键, code, name, action, quantity, target_price, status, created_at, filled_at, filled_price）
- `watchlist`（code 主键, name, group_id）
- `watchlist_groups`（id 主键, name, sort_order）
- `daily_snapshots`（id 主键, date, cash, positions_value, total）
- `price_alerts`（id 主键, code, name, condition, value, status, created_at, triggered_at, message）

## API 端点一览

### 行情（/api/market）
| 端点 | 说明 |
|------|------|
| `GET /spot` | 低价股行情列表（分页+筛选） |
| `GET /detail/{code}` | 个股详情（补充量比/52周/连涨跌/行业） |
| `GET /sectors` | 行业板块列表 |
| `GET /sector-overview` | 板块概览（均涨幅/涨跌家数/领涨股） |
| `GET /history/{code}` | K线数据（日K/周K/月K） |
| `GET /intraday/{code}` | 分时成交数据 |
| `GET /bidask/{code}` | 五档盘口数据 |
| `GET /fund-flow/{code}` | 资金流向（主力/超大单/大单/中单/小单） |
| `GET /minute/{code}` | 分钟K线（1/5/15/30/60分钟） |
| `GET /financial/abstract/{code}` | 财务摘要（同花顺） |
| `GET /financial/statement/{code}` | 三大报表（?type=利润表/资产负债表/现金流量表） |
| `GET /news/{code}` | 个股资讯 |
| `GET /indices` | 大盘指数（上证/深证/创业板） |
| `GET /ranking` | 涨跌排行（涨幅/跌幅/换手率/成交额/量比） |
| `GET /lhb` | 龙虎榜（近N日异动个股） |

### 交易（/api/trade）
| 端点 | 说明 |
|------|------|
| `GET /account` | 账户信息 |
| `GET /positions` | 持仓列表 |
| `GET /transactions` | 交易记录（日期/操作筛选） |
| `POST /buy` | 市价买入 |
| `POST /sell` | 市价卖出 |
| `POST /reset` | 重置账户 |
| `GET /watchlist` | 自选股列表（支持分组筛选） |
| `POST /watchlist/add` | 加自选 |
| `POST /watchlist/remove` | 删自选 |
| `POST /watchlist/move` | 移动分组 |
| `GET /groups` | 自选分组列表 |
| `POST /groups/create` | 创建分组 |
| `POST /groups/delete` | 删除分组 |
| `GET /market-status` | 市场交易状态 |
| `GET /dashboard` | 综合看板 |
| `POST /order` | 创建限价委托 |
| `GET /orders` | 委托列表 |
| `POST /order/{id}/cancel` | 撤销委托 |
| `POST /orders/check` | 检查并成交委托 |
| `GET /daily-snapshots` | 每日资产快照 |
| `GET /performance` | 收益统计 |
| `POST /snapshot` | 手动记录快照 |
| `POST /alert` | 创建涨跌提醒 |
| `GET /alerts` | 提醒列表 |
| `POST /alert/{id}/cancel` | 取消提醒 |

### ETF（/api/etf）
| 端点 | 说明 |
|------|------|
| `GET /spot` | ETF行情列表（分页+筛选） |
| `GET /detail/{code}` | ETF详情 |
| `GET /history/{code}` | ETF K线数据 |
| `GET /minute/{code}` | ETF分钟K线 |
| `GET /fund-flow/{code}` | ETF资金流向 |
| `GET /nav/{code}` | ETF净值 |
| `GET /holdings/{code}` | ETF持仓 |
| `GET /allocation/{code}` | ETF资产配置 |

### AI（/api/ai）
| 端点 | 说明 |
|------|------|
| `GET /analyze/{code}` | LLM综合分析（限流10次/分钟） |
| `GET /score/{code}` | 规则评分引擎（限流15次/分钟） |

## 代码约定

- API 响应使用中文 key（如 `"代码"`、`"涨跌幅"`、`"最新价"`）— 保留新浪原始字段名
- A股配色：**红涨绿跌**（与美股相反）
- 交易数量必须为100的整数倍（A股整手规则）
- `akshare` 用于分时/盘口/资金流向/分钟K线/财务数据，新浪/腾讯用于行情主数据
- 部署配置见 `DEPLOY.md`，后端 Docker + 前端 Vercel
- BDD测试使用 `pytest-bdd`，feature 文件在 `backend/tests/features/`，测试脚本在 `backend/tests/test_*_bdd.py`
- AI分析接口有IP级限流（`app/rate_limiter.py`），analyze 10次/分，score 15次/分
