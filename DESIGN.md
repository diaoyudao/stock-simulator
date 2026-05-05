# StockSimulator Design System

## Scene

散户在午休时用手机扫一眼自选，在晚上复盘时用电脑翻K线。手机屏幕亮、环境嘈杂，电脑屏幕暗、环境安静。两种场景都需要信息即刻可读，但手机版需要更大的触控目标和更少的同时信息，电脑版可以承载更宽的表格和并排面板。

深色主题是唯一选择。红涨绿跌在深色上对比度最高，这是A股产品的硬约束，不是审美偏好。

## Color Strategy

**Committed**: 一个暖调的深蓝灰底色占满整个界面，红绿双色承载核心信号（涨跌），一个冷蓝色作为交互锚点。

不用"金融蓝"（那种 navy+gold 的券商套路）。底色偏暖灰，不是纯冷蓝，这让整体感觉不像终端而更像工具。红和绿不是装饰，它们是功能色，用户靠颜色而非数字判断方向。

### Palette

| Token | OKLCH | Hex (legacy) | Role |
|---|---|---|---|
| `--bg` | oklch(0.15 0.012 260) | #0d1117 | 页面底色，暖调深蓝灰 |
| `--surface` | oklch(0.20 0.012 260) | #161b22 | 卡片、面板底色 |
| `--border` | oklch(0.30 0.010 260) | #30363d | 分隔线、输入框边框 |
| `--text` | oklch(0.78 0.010 260) | #c9d1d9 | 正文，次级文字 |
| `--text-h` | oklch(0.95 0.008 260) | #f0f6fc | 标题、高亮数值 |
| `--text-muted` | oklch(0.55 0.010 260) | #8b949e | 标签、辅助信息 |
| `--accent` | oklch(0.68 0.14 240) | #58a6ff | 交互元素：链接、按钮、活跃标签 |
| `--profit` | oklch(0.62 0.22 25) | #f85149 | 涨、买入（A股红涨） |
| `--loss` | oklch(0.65 0.18 145) | #3fb950 | 跌、卖出（A股绿跌） |
| `--buy-bg` | oklch(0.22 0.04 145) | #1a3a2a | 买入区域底色 |
| `--sell-bg` | oklch(0.22 0.05 25) | #3a1a1a | 卖出区域底色 |
| `--on-accent` | oklch(0.98 0.004 260) | #f0f6fc | 在 accent/profit/loss 色块上的文字 |

### Rules

- 红绿只用于涨跌信号和买卖操作。不要把它们用在装饰、图标、或与价格无关的地方。
- `--accent` 用于所有可交互元素：链接、按钮、活跃标签、选中状态。它是唯一的"点击这里"信号。
- `--text-muted` 用于标签和辅助文字，不用于正文。
- `--on-accent` 替代硬编码的 `#fff`，用于所有深色按钮上的文字。
- 数字用 `--text-h`（高亮）或 `--profit`/`--loss`（带方向），不用 `--text`。

## Typography

系统字体栈，不做字体加载。中文优先的排版。

```
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
  "PingFang SC", "Microsoft YaHei", sans-serif;
```

| Level | Size | Weight | Use |
|---|---|---|---|
| Detail price | 36px | 700 | 详情页股价 |
| Section title | 22px | 700 | 股票名称 |
| Body large | 16px | 600 | 涨跌额、标签值 |
| Body | 14px | 400 | 正文、表格内容 |
| Small | 13px | 400 | 筛选栏、工具栏、列表 |
| Caption | 11-12px | 400-500 | 指标标签、辅助信息 |

### Rules

- 价格数字是页面上最醒目的元素，36px/700，颜色由涨跌决定。
- 表格和列表用 13px，紧凑但不挤。
- 标签（今开、最高、成交量）用 11px + `--text-muted`，与数值拉开层次。
- 不用斜体。中文斜体不可读。

## Spacing and Rhythm

基于 4px 网格。间距不均匀，关键区域间距更大。

| Context | Gap |
|---|---|
| 页面区块之间 | 16px |
| 卡片/面板内边距 | 12-16px |
| 表格行间距 | 8px 垂直 + 10px 水平 |
| 筛选栏元素间距 | 12px |
| 按钮组间距 | 6-8px |
| 详情页价格行间距 | 16px baseline gap |

### Rules

- 同一区域内用 6-8px 紧凑间距。不同区域之间用 16px 呼吸间距。
- 详情页有 5 个视觉区域：价格行、指标网格、图表+子Tab、持仓、交易面板。区域间 16px，区域内 8-12px。

## Elevation

两层，不搞影子。

| Level | Background | Border | Use |
|---|---|---|---|
| Ground | `--bg` | none | 页面底色 |
| Raised | `--surface` | `1px solid --border` | 卡片、面板、筛选栏 |

没有第三层。不叠加 surface-on-surface（嵌套卡片是禁止的）。如果视觉上需要区分，用 border 或间距，不用更深的背景色。

## Components

### Tab Navigation

7个主 Tab，移动端固定底部，桌面端顶部水平排列。活跃态：`--accent` 背景 + `--on-accent` 文字。非活跃态：透明背景 + `--border` 边框 + `--text` 文字。

移动端：图标在上、文字在下的纵向布局。桌面端：图标+文字横向排列。

### Data Table

行情列表、持仓、交易记录、委托单都共用 `.stock-table` 样式。13px 字号，行间 1px `--border` 分隔，hover 时 `--surface` 底色高亮。

列表中的股票代码/名称是 `--accent` 色可点击链接。价格数字 600 weight。

### Metric Grid

详情页指标网格：`auto-fill, minmax(140px, 1fr)`。标签 11px `--text-muted`，数值 14px 600 `--text-h`，涨跌指标用 `--profit`/`--loss`。

用 1px `--border` 间距模拟网格线，不用 gap+border 组合。

### Chart Toolbar

两级 Tab：主 Tab（K线图/财务报表/资讯）在左，子 Tab（日K/周K/月K、MA/BOLL/MACD 等）在右。主 Tab 活跃态用底部边框指示，子 Tab 活跃态用 `--accent` 实色背景。

### Trade Panel

买入按钮：`--profit` 实色背景。卖出按钮：`--loss` 实色背景。非活跃态：`--border` 边框。确认按钮：`--accent` 实色。非交易时间：按钮半透明 + `not-allowed` 光标。

### Financial Table

横向可滚动。第一列（指标名）sticky 定位 + `--surface` 底色。12px 字号。报告期作为列头。

### News List

每条：标题（`--text`，单行省略）+ 来源/时间（`--text-muted`，不换行）。整行可点击跳转。无卡片包裹。

## Responsive

两个断点：768px（平板/大手机）、480px（小手机）。

| Feature | >768px | 480-768px | <480px |
|---|---|---|---|
| Tab 栏 | 顶部水平 | 底部固定，图标+文字 | 底部固定，图标+文字 |
| 指标网格 | auto-fill | 3列 | 2列 |
| 详情页价格 | 36px | 28px | 24px |
| 筛选栏 | 一行多控件 | 换行，控件宽度收缩 | 继续收缩 |
| 表格 | 横向滚动 | 横向滚动 | 横向滚动 |

## Anti-patterns

这些在本项目中是被禁止的：

- 嵌套卡片（surface 里面再套 surface）
- 侧边彩色条纹（border-left > 1px 作为装饰）
- 渐变文字
- 毛玻璃效果
- hero-metric 模板（大数字+小标签+渐变装饰）
- 网格卡片完全相同大小和结构
- 用 modal 替代 inline 操作
- 硬编码 `#fff`（用 `--on-accent` 替代）
- 未定义的 CSS 变量引用
