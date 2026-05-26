# A股数据链研究 — StockSimulator 数据源升级方案

> 调研日期：2026-05-19

## 核心结论

**当前混合架构方向正确，无需引入新库。** 关键动作：将 AKShare 不稳定的 `_em` 接口逐步替换为东方财富直接 HTTP API。

---

## 一、数据源现状速查

| 数据源 | 实时行情 | K线 | 分时 | 盘口 | 资金流向 | 财务 | 资讯 | 稳定性 |
|--------|---------|-----|------|------|---------|------|------|--------|
| 新浪HTTP | ✅含五档 | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | 低-中 |
| 腾讯HTTP | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | 中 |
| 东财直调HTTP | ✅含量比 | ✅ | ✅ | ❌ | ✅ | 有限 | ✅ | 中 |
| AKShare(_em) | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ✅ | ❌broken | 低 |
| AKShare(_ths/_sina) | — | — | — | — | — | ✅ | — | 中 |
| BaoStock | 有限 | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ | 高 |
| Tushare免费 | 延迟15分 | ✅ | ❌ | ❌ | ❌需积分 | ✅ | ❌ | 高 |
| pytdx | ✅ | ✅ | ❌ | 仅一档 | ❌ | ✅ | ❌ | 中 |

---

## 二、推荐方案：三层回退 + AKShare降级

### 目标架构

```
主源 → 备源1 → 备源2 → 缓存降级
```

| 数据类型 | 主源 | 备源 | AKShare角色 |
|---------|------|------|------------|
| 实时行情+五档 | 新浪HTTP | 腾讯HTTP | — |
| 量比/52周 | 腾讯HTTP | 东财直调 | — |
| 分时成交 | 东财直调HTTP | AKShare | fallback |
| 资金流向 | 东财直调HTTP | AKShare | fallback |
| 分钟K线 | 东财直调HTTP | AKShare | fallback |
| 个股资讯 | 东财搜索API | — | 已替换 |
| 日/周/月K线 | 新浪HTTP | 东财直调 | AKShare |
| 财务摘要 | AKShare(_ths) | — | 主路径(稳定) |
| 三大报表 | AKShare(_sina) | — | 主路径(稳定) |

### 东方财富直接API关键端点

```
全市场行情: https://push2.eastmoney.com/api/qt/clist/get
历史K线:   https://push2his.eastmoney.com/api/qt/stock/kline/get
个股详情:   https://push2.eastmoney.com/api/qt/stock/get
分时成交:   https://push2.eastmoney.com/api/qt/stock/trends2/get
资金流向:   https://datacenter-web.eastmoney.com/api/data/v1/get
资讯搜索:   https://search-api-web.eastmoney.com/search/jsonp
```

关键fields参数：f2(最新价) f3(涨跌幅) f8(换手率) f9(市盈率) f10(量比) f12(代码) f14(名称) f23(市净率)

---

## 三、付费方案（可选升级）

| 方案 | 年费 | 性价比 | 适合场景 |
|------|------|--------|---------|
| Tushare Pro 2000积分 | ~200元 | 最高 | 需稳定资金流向+财务 |
| 聚宽专业版 | 299元 | 中 | 需回测+数据 |
| pytdx自建 | 0 | 高(零成本) | 作为第三回退源 |

**推荐**：先做零成本优化（东财直调替换AKShare），如仍不稳定再考虑 Tushare Pro 200元/年。

---

## 四、pytdx 备选方案

pytdx 通过通达信TCP协议直连行情服务器，零成本、纯Python：

- 支持：实时行情快照、1/5/15/30/60分钟K线、财务数据
- 不支持：分时成交明细、五档盘口、资金流向、资讯
- 可作为新浪+腾讯都失败时的第三回退源
- 集成：`pip install pytdx`，TCP协议需适配层转HTTP

---

## 五、实时推送建议

当前前端轮询API。如需降低延迟：

- **推荐SSE**（Server-Sent Events）：单向推送、浏览器原生支持、比WebSocket轻量
- 架构：FastAPI后端 → 3-5秒轮询数据源 → SSE推前端
- 新浪/腾讯无官方WebSocket，SSE已够用

---

## 六、执行优先级

1. **P0 — 东财直调替换分时/资金流向/分钟K线**（效果最大，AKShare最不稳定的部分）
2. **P1 — 三层回退机制完善**（新浪→腾讯→东财→缓存）
3. **P2 — 缓存预热+熔断**（开盘前预热，连续失败自动熔断切换）
4. **P3 — pytdx集成**（作为最终回退源）
5. **P4 — SSE推送**（降低前端轮询频率）

---

## 参考来源

- [AKShare GitHub](https://github.com/akfamily/akshare)
- [Tushare Pro积分体系](https://tushare.pro/document/1?doc_id=39)
- [BaoStock文档](http://baostock.com/baostock/index.php/Python_API)
- [pytdx GitHub](https://github.com/rainx/pytdx)
- [efinance GitHub](https://github.com/Micro-sheep/efinance)
