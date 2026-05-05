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

暂无测试套件（BDD 测试在 `backend/tests/` 目录，使用 pytest-bdd）。

## 架构

```
浏览器 → Vite :5173 ──代理 /api──→ FastAPI :8000 → 新浪/腾讯 API
                                         │
                                         ▼
                                    SQLite (backend/data/stock_sim.db)
```

### 后端（`backend/`）

- **入口**：`main.py` — FastAPI 应用，CORS 已开启，两个路由模块
- **路由**：`app/routers/market.py`（15个端点）和 `app/routers/trade.py`（12个端点）
- **服务层**：
  - `app/services/market_data.py` — 数据获取、缓存、两轮筛选策略、分时/盘口/资金流向/分钟K线/财务/资讯
  - `app/services/trading.py` — 账户管理、买卖交易、SQLite 持久化（aiosqlite）

### 前端（`frontend/`）

- 单文件 React 应用：`src/App.tsx`（~2000行），`src/api.ts` 为 API 客户端（带GET缓存）
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

### 行情（market.py）
| 端点 | 说明 |
|------|------|
| `GET /market/spot` | 低价股行情列表（分页+筛选） |
| `GET /market/detail/{code}` | 个股详情（补充量比/52周/连涨跌/行业） |
| `GET /market/sectors` | 行业板块列表 |
| `GET /market/sector-overview` | 板块概览（均涨幅/涨跌家数/领涨股） |
| `GET /market/history/{code}` | K线数据（日K/周K/月K） |
| `GET /market/intraday/{code}` | 分时成交数据 |
| `GET /market/bidask/{code}` | 五档盘口数据 |
| `GET /market/fund-flow/{code}` | 资金流向（主力/超大单/大单/中单/小单） |
| `GET /market/minute/{code}` | 分钟K线（1/5/15/30/60分钟） |
| `GET /market/financial/abstract/{code}` | 财务摘要（同花顺） |
| `GET /market/financial/statement/{code}` | 三大报表（?type=利润表/资产负债表/现金流量表） |
| `GET /market/news/{code}` | 个股资讯 |
| `GET /market/indices` | 大盘指数（上证/深证/创业板） |
| `GET /market/ranking` | 涨跌排行（涨幅/跌幅/换手率/成交额/量比） |
| `GET /market/lhb` | 龙虎榜（近N日异动个股） |

### 交易（trade.py）
| 端点 | 说明 |
|------|------|
| `GET /trade/account` | 账户信息 |
| `GET /trade/positions` | 持仓列表 |
| `GET /trade/transactions` | 交易记录（日期/操作筛选） |
| `POST /trade/buy` | 市价买入 |
| `POST /trade/sell` | 市价卖出 |
| `POST /trade/reset` | 重置账户 |
| `GET /trade/watchlist` | 自选股列表（支持分组筛选） |
| `POST /trade/watchlist/add` | 加自选 |
| `POST /trade/watchlist/remove` | 删自选 |
| `POST /trade/watchlist/move` | 移动分组 |
| `GET /trade/groups` | 自选分组列表 |
| `POST /trade/groups/create` | 创建分组 |
| `POST /trade/groups/delete` | 删除分组 |
| `GET /trade/market-status` | 市场交易状态 |
| `GET /trade/dashboard` | 综合看板 |
| `POST /trade/order` | 创建限价委托 |
| `GET /trade/orders` | 委托列表 |
| `POST /trade/order/{id}/cancel` | 撤销委托 |
| `POST /trade/orders/check` | 检查并成交委托 |
| `GET /trade/daily-snapshots` | 每日资产快照 |
| `GET /trade/performance` | 收益统计 |
| `POST /trade/snapshot` | 手动记录快照 |
| `POST /trade/alert` | 创建涨跌提醒 |
| `GET /trade/alerts` | 提醒列表 |
| `POST /trade/alert/{id}/cancel` | 取消提醒 |

## 代码约定

- API 响应使用中文 key（如 `"代码"`、`"涨跌幅"`、`"最新价"`）— 保留新浪原始字段名
- A股配色：**红涨绿跌**（与美股相反）
- 交易数量必须为100的整数倍（A股整手规则）
- `akshare` 用于分时/盘口/资金流向/分钟K线/财务数据，新浪/腾讯用于行情主数据
- 部署配置见 `DEPLOY.md`，后端 Docker + 前端 Vercel
