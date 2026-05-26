# AI选股 / 智能筛选 开源项目调研报告

> 调研日期: 2026-05-21
> 目标: 寻找可集成到 StockSimulator (FastAPI + React) 的 AI/智能选股方案
> 重点: A股低价股(<5元)、LLM/AI驱动、轻量级、Python

---

## 一、现有能力基线 (StockSimulator 已有)

在调研之前，先明确项目已有的AI分析能力（`backend/app/services/ai_analysis.py`）:

| 能力 | 实现方式 | 说明 |
|------|---------|------|
| LLM综合分析 | 智谱GLM-4-flash API | 技术面+基本面+资金面+风险，返回JSON |
| 规则评分引擎 | 纯Python规则 | MA/RSI/MACD + PE/PB + 资金流向，三维度加权打分(0-100) |
| 数据源 | 新浪/腾讯/AKShare | 行情、K线、资金流、财务、资讯全覆盖 |

**缺口**: 缺少 **批量选股/排序** 能力 -- 当前只能对单只股票评分，无法对全市场或低价股池进行批量排名和筛选。

---

## 二、重点推荐项目

### 1. Qlib (Microsoft) -- 最成熟的量化因子库

| 属性 | 值 |
|------|-----|
| **Repo** | https://github.com/microsoft/qlib |
| **Stars** | ~17k+ |
| **License** | MIT |
| **最后更新** | 活跃维护中 (2025-2026持续更新) |
| **技术栈** | Python, PyTorch, LightGBM, pandas, numpy |

**核心功能:**
- **Alpha158 因子库**: 158个标准化技术因子（动量/波动率/成交量/价格等），A股验证
- **多因子模型框架**: LightGBM/XGBoost 集成学习，自动特征工程
- **数据层**: 内置A股数据接口（支持自定义数据源接入）
- **模型库**: 预训练模型 + 自定义模型支持
- **回测框架**: 完整的模拟交易和策略评估
- **研究工作流**: Dataset -> Model -> Backtest -> Analysis 全链路

**适合场景:** 作为因子计算和排序引擎。可以用其 Alpha158 因子库为低价股池生成因子值，再用内置模型打分排序。

**集成难度: 中高**
- 优点: 成熟稳定，文档完善，社区活跃
- 缺点: 依赖较重（PyTorch/LightGBM），需要数据预处理适配
- 建议: 只取其因子计算模块（`qlib/contrib/data/handler.py`），不引入完整回测框架

**与StockSimulator集成思路:**
```
低价股池(500只) -> Qlib Alpha158因子 -> LightGBM打分 -> Top-N排序 -> 前端展示
```

---

### 2. FinRL (AI4Finance Foundation) -- 强化学习交易

| 属性 | 值 |
|------|-----|
| **Repo** | https://github.com/AI4Finance-Foundation/FinRL |
| **Stars** | ~12k+ |
| **License** | MIT |
| **最后更新** | 活跃维护中 |
| **技术栈** | Python, PyTorch, Gym, Stable-Baselines3, ElegantRL |

**核心功能:**
- 强化学习(DRL)股票交易：PPO/DQN/DDPG/SAC/TD3 等算法
- 预训练金融大模型：FinGPT / FinMA / ChatGLM-fin 等
- **股票筛选/排序**: FinRL-Online 包含状态特征工程和动作空间设计
- 多市场支持：美股/加密货币/A股/期货
- 风险管理：仓位控制、止损止盈
- 可视化：回测曲线、收益分布

**适合场景:** 如果要做更高级的"AI选股+自动交易决策"，FinRL提供完整的RL pipeline。

**集成难度: 高**
- 优点: 学术界最流行的金融RL框架，论文支撑强
- 缺点: 学习曲线陡峭，依赖重（PyTorch + Gym + 多个RL库），更适合研究而非生产
- 建议: 参考其特征工程部分（`finrl/meta/preprocessors`）用于构建选股特征

---

### 3. mlstock (LamtechHQ) -- 轻量级多因子选股

| 属性 | 值 |
|------|-----|
| **Repo** | https://github.com/LamtechHQ/mlstock |
| **Stars** | ~500+ |
| **License** | MIT |
| **最后更新** | 较新 |
| **技术栈** | Python, pandas, scikit-learn, tushare/akshare |

**核心功能:**
- **多因子选股**: 动量/价值/成长/质量/波动率等多维度因子
- **机器学习打分**: XGBoost/LightGBM/随机森林排序模型
- **A股原生**: 直接使用 tushare/akshare 获取数据
- **回测评估**: 选股策略效果检验
- **轻量**: 相比Qlib/FinRL简单很多，代码量小

**集成难度: 低~中**
- 优点: 轻量、专注选股（非完整量化平台）、A股原生
- 缺点: 社区较小，文档不如Qlib完善
- 建议: **最接近StockSimulator需求的方案**，可直接参考其因子定义和打分逻辑

---

### 4. VnPy (vnpy/vnpy) -- 专业量化交易平台

| 属性 | 值 |
|------|-----|
| **Repo** | https://github.com/vnpy/vnpy |
| **Stars** | ~25k+ |
| **License** | MIT |
| **最后更新** | 活跃维护 |
| **技术栈** | Python, PyQt/PySide, MongoDB/SQLite, event-driven |

**核心功能:**
- 完整量化交易平台：CTP gateway / 行情 / 交易 / 回测
- **策略引擎**: 支持选股类策略（通过脚本策略模块）
- 多品种: 期货/股票/期权/外盘
- GUI界面: 专业交易终端
- 插件架构: APP扩展机制

**适合场景:** 不太适合直接集成。VnPy是完整的交易平台，过于重量级。

**集成难度: 高（不推荐）**
- VnPy是独立应用，不是库，难以作为模块嵌入FastAPI
- 但可以参考其选股策略的设计模式

---

### 5. Backtrader (mementum/backtrader) -- 经典回测框架

| 属性 | 值 |
|------|-----|
| **Repo** | https://github.com/mementum/backtrader |
| **Stars** | ~13k+ |
| **License** | GNU GPL v3 |
| **最后更新** | 维护模式（作者已宣布停止开发） |
| **技术栈** | Python, pandas, matplotlib |

**核心功能:**
- 经典回测框架：事件驱动的回测引擎
- **指标库**: 100+ 技术指标（TA-Lib封装）
- 策略模板: 支持编写选股/择时策略
- 数据源: 多种格式支持
- 可视化: 内置绘图

**适合场景:** 可以用其丰富的技术指标库来增强评分因子的丰富度。

**集成难度: 中**
- 优点: 成熟稳定，指标库丰富
- 缺点: GPL协议需注意；已停止开发；同步阻塞式设计不适合async FastAPI
- 建议: 仅参考其指标计算逻辑，不直接依赖

---

## 三、其他值得关注的项目

### 6. Zipline (quantopian/zipline)

| 属性 | 值 |
|------|-----|
| **Repo** | https://github.com/quantopian/zipline |
| **Stars** | ~18k+ |
| **状态** | Quantopian关闭后社区维护，基本停滞 |
| **说明** | Quantopian遗产，回测框架经典，但不再推荐新项目使用 |

### 7. JQData / JoinQuant (聚宽)

| 属性 | 值 |
|------|-----|
| **Repo** | https://github.com/JoinQuant/jqdatasdk |
| **性质** | 商业API SDK（非纯开源） |
| **说明** | 聚宽研究平台的Python SDK，因子库丰富但需付费账号 |

### 8. Tushare Pro (tusharepro)

| 属性 | 值 |
|------|-----|
| **Repo** | https://github.com/tusharePro/tushare (旧版) |
| **性质** | 数据API（非选股框架） |
| **说明** | StockSimulator已在用AKShare，Tushare可作为补充数据源 |

### 9. 各种个人/小型项目

GitHub上大量 "AI选股"、"智能选股" 项目多为:
- 课程作业/毕设项目（质量参差不齐）
- 使用简单的均线交叉/MACD策略包装成"AI"
- 缺乏持续维护
- 不建议直接使用，但可参考思路

搜索到的关键词包括: `stock-screener`, `ai-stock-picker`, `smart-invest`, `alpha-mining`, `factor-investing`

---

## 四、对比总结

| 项目 | Stars | 选股能力 | 轻量度 | A股支持 | 推荐集成度 |
|------|-------|---------|--------|---------|-----------|
| **Qlib** | 17k+ | ★★★★★ | ★★☆☆☆ | ★★★★☆ | ★★★★☆ (取因子模块) |
| **FinRL** | 12k+ | ★★★☆☆ | ★☆☆☆☆ | ★★★☆☆ | ★★★☆☆ (参考特征工程) |
| **mlstock** | 500+ | ★★★★☆ | ★★★★☆ | ★★★★★ | ★★★★★ (**首选**) |
| **VnPy** | 25k+ | ★★★☆☆ | ★☆☆☆☆ | ★★★★★ | ★★☆☆☆ (过重) |
| **Backtrader** | 13k+ | ★★★☆☆ | ★★★☆☆ | ★★★☆☆ | ★★★☆☆ (仅指标库) |

---

## 五、推荐集成方案

### 方案A: mlstock风格的自建多因子评分 (推荐)

**理由**: 最贴合需求、最轻量、完全可控

**实现路径:**
1. 定义5-10个核心因子（基于已有数据源）:
   - 动量因子: 5日/20日/60日涨跌幅
   - 波动率因子: 20日收益率标准差
   - 流动性因子: 换手率均值、成交额/市值比
   - 价值因子: PE/PB/PS 分位数
   - 质量因子: ROE、毛利率趋势
   - 资金面因子: 主力净流入/流出强度
   - 技术因子: MA排列、RSI位置、MACD信号
   - 连板因子: 连涨/连跌天数

2. 对低价股池（<5元，约200-500只）批量计算因子值
3. 加权综合打分（可配置权重）
4. Top-N 排序输出
5. 前端展示"AI精选"列表页

**工作量估计**: 3-5天（因子定义+计算+排序+前端）

**优势:**
- 无额外重依赖（pandas/numpy足够）
- 与现有数据源无缝衔接（新浪/腾讯/AKShare）
- 完全异步兼容（可在线程池中跑因子计算）
- 可解释性强（每个因子含义清晰）

### 方案B: Qlib Alpha158因子 + 轻量模型

**理由**: 因子体系专业成熟

**实现路径:**
1. 安装 qlib（仅取 data handler 和 alpha158 因子定义）
2. 用已有行情数据构造 Qlib 格式数据
3. 计算 Alpha158 因子值
4. 用预训练 LightGBM 模型或简单加权打分
5. 排序输出

**工作量估计**: 1-2周（含数据格式转换和调试）

**优势:**
- 158个经过学术验证的因子
- 微软团队维护，质量保证
- 有预训练模型可直接用

**劣势:**
- 引入 PyTorch/LightGBM 重依赖
- 数据格式转换工作量大
- Docker镜像会变大

### 方案C: 增强现有评分引擎为批量模式

**理由**: 改动最小，渐进增强

**实现路径:**
1. 将现有 `_compute_score()` 扩展为批量版本 `batch_score(codes: list)`
2. 增加更多因子到评分逻辑中
3. 新增 `/api/ai/screen` 端点，接受筛选条件，返回排序结果
4. 前端新增"智能选股"Tab

**工作量估计**: 1-2天

**优势:**
- 最快见效
- 零新增依赖
- 渐进式改进

**劣势:**
- 因子丰富度有限（相比Qlib的158个）
- 打分逻辑较简单（无ML模型）

---

## 六、最终建议

**短期（1-3天）: 采用方案C**
- 将现有评分引擎批量化，增加5-8个因子
- 快速上线"智能选股"基础版
- 验证用户需求和交互流程

**中期（1-2周）: 向方案A演进**
- 参考mlstock的因子体系，重构评分引擎
- 加入因子权重可配置、因子IC/IR分析
- 增加因子热力图等可视化

**长期（可选）: 评估方案B**
- 如果用户反馈需要更专业的因子体系
- 引入Qlib Alpha158因子作为高端选项

---

## 七、关键链接汇总

| 项目 | URL |
|------|-----|
| Qlib | https://github.com/microsoft/qlib |
| FinRL | https://github.com/AI4Finance-Foundation/FinRL |
| mlstock | https://github.com/LamtechHQ/mlstock |
| VnPy | https://github.com/vnpy/vnpy |
| Backtrader | https://github.com/mementum/backtrader |
| Zipline | https://github.com/quantopian/zipline |
| AKShare (已在用) | https://github.com/akfamily/akshare |
| StockSimulator (本项目) | 当前仓库 |

---

*本报告由 WebSearch 自动调研生成，建议对最终选型项目做深入的代码审查后再决定集成方案。*
