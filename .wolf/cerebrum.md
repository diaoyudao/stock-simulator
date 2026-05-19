# Cerebrum

> OpenWolf's learning memory. Updated automatically as the AI learns from interactions.
> Do not edit manually unless correcting an error.
> Last updated: 2026-05-13

## User Preferences

<!-- How the user likes things done. Code style, tools, patterns, communication. -->

## Key Learnings

- **Project:** StockSimulator
- **Description:** 筛选5元以下A股，展示新浪财经实时行情，10万虚拟资金模拟交易。
- **数据源架构（2026-05-19重构）**：astock_data.py（基于a-stock-data项目）作为主数据源层，mootdx(TCP)用于K线/盘口/分时/财务，腾讯HTTP用于PE/PB/量比，百度HTTP用于资金流向，东财HTTP用于资讯/龙虎榜/排行，新浪HTTP用于财报三表。AKShare降级为fallback。
- **资金流向数据源（2026-05-19修复）**：push2his.eastmoney.com 被Clash fake-ip DNS污染(198.18.1.47)且SSL握手失败，fundflow2.eastmoney.com 返回HTML非JSON。唯一可用源：datacenter-web.eastmoney.com RPT_DMSK_TS_STOCKNEW（仅当天数据）。使用文件缓存(`backend/data/fund_flow/{code}.json`)积累历史数据。ETF无datacenter数据。
- **板块数据源（2026-05-19统一改为申万行业）**：板块模块统一用申万行业分类，不再用概念板块。`get_sector_list()` 和 `get_sector_overview()` 都返回申万行业数据（31一级+129二级，硬编码代码映射）。`ak.sw_index_first/second_info()` 不可用（legulegu.com 504），成分股查询用 `ak.index_component_sw(code)` 仍可用。`/api/market/sectors` 和 `/api/market/sector-overview` 接口路径不变，`/api/market/industries` 也返回申万行业。
- **mootdx是同步库**：所有调用需 `run_in_executor` 包装。FastAPI中需注意 `asyncio.get_event_loop().is_running()` 判断，运行中用 ThreadPoolExecutor。
- **东财JSONP接口**：`eastmoney_stock_news` 的 `cmsArticleWebOld` 返回值可能是 list 或 dict，需兼容两种格式。

## Do-Not-Repeat

- **[2026-05-19] 同步函数中禁止用 `loop.run_in_executor` 不 await**：在 FastAPI 同步路由调用的函数中，`loop.run_in_executor()` 返回 future 但没 await，导致变量变成 coroutine 对象而非结果。正确做法：用 `pool.submit(lambda: asyncio.run(...)).result()` 阻塞等待。旧模式 `if loop.is_running(): run_in_executor else: asyncio.run` 在同步路由中走 else 正常，在 async 路由中走 if 但返回未 await 的 future，静默失败。
- **[2026-05-19] 腾讯行情指数前缀问题**：指数代码 `000001` 在 `_tencent_quote_sync` 中被加 `sz` 前缀变成 `sz000001`（平安银行），实际应为 `sh000001`（上证指数）。指数需单独处理，不走通用前缀逻辑。
- **[2026-05-19] 东财spot API字段类型**：`f14`（名称）是字符串，不能走 `_safe_float()` 转换。`代码` 和 `名称` 都需要 `str()` 而非 float。
- **[2026-05-19] push2his.eastmoney.com不可用**：Clash Verge fake-ip模式导致DNS污染（解析到198.18.1.47），走DIRECT不通，走代理SSL握手也失败。fundflow2.eastmoney.com返回HTML非JSON（服务端拒绝）。资金流向必须用datacenter-web.eastmoney.com的RPT_DMSK_TS_STOCKNEW（仅当天）+ 本地文件历史缓存。
- **[2026-05-19] 申万行业代码格式**：`ak.sw_index_second_info()` 已不可用（legulegu.com 504），改用硬编码 `_SW_L1_INDUSTRIES` / `_SW_L2_INDUSTRIES` 映射。成分股查询用 `ak.index_component_sw(code)` 只接受纯数字如 "801102"。
- **[2026-05-19] 板块统一用申万行业**：概念板块（光通信模块等）不再支持筛选。成分股查询只用 `ak.index_component_sw()`，通过硬编码映射查行业代码。

## Decision Log

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->
