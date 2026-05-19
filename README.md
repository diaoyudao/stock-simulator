# A股低价股模拟炒股

筛选5元以下A股，展示实时行情，10万虚拟资金模拟交易。

## 功能

- **行情筛选** — 价格/涨跌幅/换手率/市盈率/市净率/量比等多维度筛选，支持申万行业过滤
- **实时行情** — 新浪财经+腾讯API+东财HTTP多数据源，60秒刷新，双源回退
- **模拟交易** — 10万虚拟资金，A股整手规则，限价委托单
- **K线图表** — 日K/周K/月K，MA/BOLL/MACD/KDJ/RSI指标
- **ETF行情** — ETF筛选/详情/持仓/净值/资产配置
- **自选股** — 分组管理，实时价格追踪
- **行业板块** — 申万行业分类（31一级+129二级），同花顺资金流数据，主力净额+强度分析
- **收益分析** — 资金曲线、收益率统计、大盘指数对比
- **涨跌提醒** — 价格到达/跌破目标价自动通知，30秒轮询检查
- **AI分析** — LLM综合分析 + 规则评分引擎（限流保护）
- **移动端适配** — 响应式布局，手机平板均可使用

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | React 19 + TypeScript + Vite + TradingView Lightweight Charts |
| 后端 | FastAPI + aiosqlite + requests |
| 数据源 | astock_data(主) + mootdx(TCP) + 腾讯HTTP + 百度HTTP + 东财HTTP + 新浪HTTP + AKShare(降级) |
| 数据库 | SQLite |

## 数据源架构

| 数据 | 主源 | 降级源 |
|------|------|--------|
| 实时行情 | 东财push2(SPA) | 新浪分页 |
| 量比/52周 | 腾讯HTTP | — |
| K线/盘口/分时 | mootdx TCP | AKShare |
| PE/PB | 腾讯HTTP | — |
| 资金流向 | datacenter-web | 文件缓存历史 |
| 行业板块 | 同花顺资金流 | 申万行业(AKShare) |
| 财务报表 | 新浪HTTP | AKShare |
| 个股资讯 | 东财HTTP | — |

## 快速开始

```bash
# 一键启停（推荐）
bash dev.sh start          # 启动前后端
bash dev.sh stop           # 停止
bash dev.sh restart        # 重启

# 或手动启动
cd backend && source .venv/Scripts/activate && uvicorn main:app --reload
cd frontend && npm install && npm run dev
```

访问 http://localhost:5173

## 项目结构

```
backend/
  main.py                       # FastAPI 入口
  app/routers/market.py         # 行情接口（15个端点）
  app/routers/trade.py          # 交易接口（24个端点）
  app/routers/etf.py            # ETF接口（8个端点）
  app/routers/ai.py             # AI分析接口（2个端点）
  app/services/market_data.py   # 数据获取+缓存+两轮筛选
  app/services/trading.py       # 交易逻辑+SQLite持久化
  app/services/ai_analysis.py   # LLM分析+规则评分引擎
  app/services/astock_data.py   # 数据源层(a-stock-data)
frontend/
  src/App.tsx                   # 主应用
  src/components/StockDetail.tsx # 详情页
  src/api.ts                    # API客户端（带缓存）
  src/utils/indicators.ts       # 技术指标计算
```

## 性能优化

- 两轮筛选策略：基础筛选→仅对pre_filtered补腾讯量比/52周数据
- 行情缓存后台预热，用户无冷加载等待
- 资金流向文件缓存积累历史（datacenter仅返回当天数据）
- 股票代码O(1)索引 + 价格映射缓存
- 前端筛选400ms防抖 + GET请求30秒内存缓存

## 部署

详见 [DEPLOY.md](DEPLOY.md)

**最低成本：165元/年**（腾讯云/阿里云轻量2核4G + Vercel免费托管前端）

## License

MIT
