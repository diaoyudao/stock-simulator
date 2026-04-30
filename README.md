# A股低价股模拟炒股

筛选5元以下A股，展示新浪财经实时行情，10万虚拟资金模拟交易。

## 功能

- **行情筛选** — 价格/涨跌幅/换手率/市盈率/市净率/量比等多维度筛选，支持行业过滤
- **实时行情** — 新浪财经+腾讯API双数据源，60秒刷新
- **模拟交易** — 10万虚拟资金，A股整手规则，限价委托单
- **K线图表** — 日K/周K/月K，MA/BOLL/MACD/KDJ/RSI指标
- **自选股** — 分组管理，实时价格追踪
- **行业板块** — 48个行业涨跌概览，领涨股展示
- **收益分析** — 资金曲线、收益率统计、大盘指数对比
- **涨跌提醒** — 价格到达/跌破目标价自动通知
- **移动端适配** — 响应式布局，手机平板均可使用

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | React 19 + TypeScript + Vite + TradingView Lightweight Charts |
| 后端 | FastAPI + aiosqlite + requests |
| 数据源 | 新浪财经 API + 腾讯行情 API |
| 数据库 | SQLite |

## 快速开始

```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload

# 前端
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

## 项目结构

```
backend/
  main.py                  # FastAPI 入口
  app/routers/market.py    # 行情接口（筛选/详情/K线/板块/指数）
  app/routers/trade.py     # 交易接口（账户/持仓/买卖/委托/提醒）
  app/services/market_data.py  # 数据获取+缓存
  app/services/trading.py      # 交易逻辑+SQLite持久化
frontend/
  src/App.tsx              # 全部UI组件
  src/App.css              # 样式（含移动端适配）
  src/api.ts               # API客户端（带缓存）
  src/utils/indicators.ts  # 技术指标计算
```

## 性能优化

- 行情缓存后台预热，用户无冷加载等待
- async端点同步IO改为线程池执行，不阻塞事件循环
- 股票代码O(1)索引 + 价格映射缓存
- K线/指数数据60秒缓存
- 数据库共享连接，去掉每次请求建表
- 前端筛选400ms防抖 + GET请求30秒内存缓存

## 部署

详见 [DEPLOY.md](DEPLOY.md)

**最低成本：165元/年**（腾讯云/阿里云轻量2核4G + Vercel免费托管前端）

## 界面预览

A股配色：红涨绿跌

- 行情筛选页 — 多维度过滤 + 排序
- 股票详情页 — K线图 + 技术指标 + 交易面板
- 收益分析页 — 资金曲线 + 大盘对比

## License

MIT
