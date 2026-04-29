# Agent Progress — StockSimulator

## Session 1 — 2026-04-29

### Done
- 框架文档初始化（task_tree.json, agent_progress.md, init.sh）
- 项目初始化：FastAPI 后端 + React 前端脚手架
- 行情筛选页：价格/涨跌幅/换手率/成交额等基础筛选
- 交易系统：买入/卖出/持仓/交易记录，SQLite 持久化
- 账户管理：初始资金10万，重置功能

### Decisions
- **数据源**: 初始选择 AKShare，后因网络不通切换至新浪财经API
- **后端**: FastAPI，Python 生态与数据源无缝集成
- **前端**: React + Vite + TypeScript，标准现代 Web 技术栈
- **数据库**: SQLite（aiosqlite），轻量无需部署
- **缓存**: 内存缓存（TTL 60秒），避免频繁请求触发反爬

### Discovered
- AKShare / 东方财富 API 因网络问题无法访问
- 新浪财经 API 分页获取全市场A股可行（~80页，每页80条）
- 终端中文显示乱码，但 JSON/API 响应数据正确

## Session 2 — 2026-04-29

### Done
- 交易记录按日期范围 + 操作类型筛选
- 后端 `_fetch_all_stocks()` 分页获取 + 字段映射
- 前端交易记录页日期筛选UI

## Session 3 — 2026-04-29

### Done
- 股票详情页（参考同花顺）：头部行情、指标网格、K线图、持仓信息、交易面板
- TradingView lightweight-charts 集成（日K/周K/月K）
- 新浪字段补充：今开/最高/最低/昨收/买一/卖一/市净率/流通市值
- 行情列表/持仓页股票代码可点击跳转详情
- 月K线：从日K数据聚合（新浪不支持月K scale）

### Discovered
- 新浪 `scale=5200` 和 `scale=14400` 不支持，月K需从日K聚合
- K线图中国A股配色：红涨绿跌（与美股相反）

## Session 4 — 2026-04-29

### Done
- 第一批筛选项：换手率、成交额、市盈率、市净率、总市值、流通市值、振幅、ST过滤、关键词搜索
- 额外数据源筛选：
  - 行业板块筛选（新浪行业列表 + 代码→行业映射，48个行业）
  - 量比筛选（腾讯API批量获取）
  - 52周新高/新低筛选（腾讯API）
  - 连涨/连跌天数（日K线计算，详情页展示）
- 两轮筛选优化：先基础过滤，再仅对预筛选结果补充腾讯数据
- 买入/卖出优化：不再调用 get_stock_detail()，改用缓存行情数据查价格

### Decisions
- **两轮筛选策略**: 基础条件先过滤（Sina数据），再仅对少量结果补充腾讯数据，避免对5000+股票全量调用腾讯API
- **行业映射限制**: 只取前30个行业构建映射，控制请求量
- **52周新高/新低阈值**: 当前价 ≥ 52周最高×0.95 为新高，当前价 ≤ 52周最低×1.05 为新低

### Discovered
- 量比/52周数据需从腾讯API获取，新浪行情不包含
- 全量调用腾讯API（5000+股票）导致120秒超时，必须两轮筛选
- 新浪行业板块JSON嵌套7层，需递归解析 `[名称, '', 'new_xxx']` 模式

### Risks
- 新浪分页获取全市场数据约需60秒（冷启动），缓存60秒内复用
- 行业映射构建需请求30个行业页面，约10秒，缓存5分钟
- 腾讯API单次最多约50只，批量需分片+0.5秒间隔

### Current State
- 后端：FastAPI 运行在 127.0.0.1:8000
- 前端：Vite dev server 运行在 127.0.0.1:5173
- 所有 API 端点已验证通过
- 筛选器：基础9项 + 额外4项（行业/量比/52周/连涨跌）全部可用

## Session 5 — 2026-04-29

### Done
- 交易时间限制：按A股交易时间（周一至周五 9:30-11:30, 13:00-15:00）禁用交易按钮
- 前端 `useTradingTime()` hook：每30秒检查，返回交易状态（休市/尚未开盘/交易中/午间休市/已收盘）
- 4处按钮禁用：TradeButton、详情页买入/卖出/确认按钮
- 详情页交易面板：非交易时间显示红色状态提示
- 后端 `_check_trading_time()` 校验：buy/sell 端点拒绝非交易时间请求
- CSS：disabled 按钮样式 + 交易状态提示样式
- Git 推送到 Gitee
- 创建 CLAUDE.md 项目指引文档

### Decisions
- **前后端双重校验**：前端禁用按钮 + 后端拒绝请求，防止绕过前端限制
- **节假日暂不处理**：中国法定节假日规则复杂且每年变化，当前只判断周末

### Risks
- 未处理法定节假日，节假日仍允许交易（可后续接入节假日API）

## Session 6 — 2026-04-29

### Done
- 接口速度优化：ThreadPoolExecutor 并发请求
  - `_fetch_all_stocks()`：80页串行→8线程并发，60秒→8.5秒
  - `_fetch_sector_mapping()`：30行业串行→48行业并发，10秒→2秒
  - `_fetch_tencent_batch()`：分片并发请求腾讯API
- 移除所有 `time.sleep(0.3)` 间隔（并发模式下无需节流）

### Decisions
- **ThreadPoolExecutor max_workers=8**：新浪API并发8线程稳定，更高可能触发限制
- **行业映射扩展到48个**：并发后速度快，不再限制为30个

### Discovered
- lightweight-charts v5 移除了 `addCandlestickSeries()` / `addHistogramSeries()`，需改用 `addSeries(CandlestickSeries, opts)` / `addSeries(HistogramSeries, opts)`

## Session 7 — 2026-04-29

### Done
- 行业板块面板：新增第4个tab"行业板块"
  - 后端 `get_sector_overview()`：按行业聚合全市场行情，计算均涨幅、涨跌家数、成交额、52周新高新低数、领涨股top3
  - 后端 `GET /market/sector-overview` 端点
  - 前端 `SectorsTab` 组件：表格展示48个板块概览
  - 领涨股名称可点击跳转详情页
- K线图首次加载修复：`requestAnimationFrame` 等容器布局完成
- 涨跌幅颜色修正：红涨绿跌（A股惯例），修正 CSS 变量 `--profit`/`--loss`
- 权限配置精简：55条冗余规则→20条通配符

### Risks
- 板块概览接口耗时约12秒（需全量腾讯API补充52周数据），可考虑异步或缓存
