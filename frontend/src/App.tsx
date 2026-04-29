import { useState, useEffect, useCallback, useRef } from "react";
import { api, type StockItem, type StockDetail, type KLineItem, type AccountInfo, type Position, type Transaction } from "./api";
import { createChart, CandlestickSeries, HistogramSeries, type IChartApi, type CandlestickData, type HistogramData, ColorType } from "lightweight-charts";
import "./App.css";

type Tab = "market" | "positions" | "transactions";

function useTradingTime() {
  const [info, setInfo] = useState(() => checkTradingTime());
  useEffect(() => {
    const id = setInterval(() => setInfo(checkTradingTime()), 30000);
    return () => clearInterval(id);
  }, []);
  return info;
}

function checkTradingTime() {
  const now = new Date();
  const day = now.getDay(); // 0=Sun, 6=Sat
  const h = now.getHours(), m = now.getMinutes(), t = h * 60 + m;
  if (day === 0 || day === 6) return { isTradingTime: false, tradingStatus: "休市（周末）" };
  if (t < 9 * 60 + 30) return { isTradingTime: false, tradingStatus: "尚未开盘" };
  if (t <= 11 * 60 + 30) return { isTradingTime: true, tradingStatus: "交易中" };
  if (t < 13 * 60) return { isTradingTime: false, tradingStatus: "午间休市" };
  if (t <= 15 * 60) return { isTradingTime: true, tradingStatus: "交易中" };
  return { isTradingTime: false, tradingStatus: "已收盘" };
}

export default function App() {
  const [tab, setTab] = useState<Tab>("market");
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [txFilter, setTxFilter] = useState<{ start_date?: string; end_date?: string; action?: string }>({});
  const [selectedStock, setSelectedStock] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [acc, pos, txs] = await Promise.all([
      api.getAccount(),
      api.getPositions(),
      api.getTransactions({ limit: 200, ...txFilter }),
    ]);
    setAccount(acc);
    setPositions(pos);
    setTransactions(txs);
  }, [txFilter]);

  const handleTxFilter = useCallback((start: string, end: string, action: string) => {
    setTxFilter({
      ...(start ? { start_date: start } : {}),
      ...(end ? { end_date: end } : {}),
      ...(action ? { action } : {}),
    });
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (selectedStock) {
    return (
      <StockDetail
        code={selectedStock}
        positions={positions}
        onBack={() => setSelectedStock(null)}
        onTrade={refresh}
      />
    );
  }

  return (
    <div className="app">
      <header className="header">
        <h1>A股低价股模拟炒股</h1>
        {account && <AccountBar account={account} />}
      </header>
      <nav className="tabs">
        {(["market", "positions", "transactions"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t === "market" ? "行情筛选" : t === "positions" ? "我的持仓" : "交易记录"}
          </button>
        ))}
        <button className="tab reset" onClick={async () => { await api.reset(); refresh(); }}>重置账户</button>
      </nav>
      <main className="main">
        {tab === "market" && <MarketTab onTrade={refresh} onSelectStock={setSelectedStock} />}
        {tab === "positions" && <PositionsTab positions={positions} onTrade={refresh} onSelectStock={setSelectedStock} />}
        {tab === "transactions" && <TransactionsTab transactions={transactions} onFilter={handleTxFilter} />}
      </main>
    </div>
  );
}

function AccountBar({ account }: { account: AccountInfo }) {
  const profitClass = account.total_profit >= 0 ? "profit" : "loss";
  return (
    <div className="account-bar">
      <div><span className="label">总资产</span><span className="value">¥{account.total_assets.toLocaleString()}</span></div>
      <div><span className="label">现金</span><span className="value">¥{account.cash.toLocaleString()}</span></div>
      <div><span className="label">持仓市值</span><span className="value">¥{account.market_value.toLocaleString()}</span></div>
      <div><span className="label">盈亏</span><span className={`value ${profitClass}`}>{account.total_profit >= 0 ? "+" : ""}¥{account.total_profit.toLocaleString()} ({account.profit_pct.toFixed(2)}%)</span></div>
    </div>
  );
}

function MarketTab({ onTrade, onSelectStock }: { onTrade: () => void; onSelectStock: (code: string) => void }) {
  const [stocks, setStocks] = useState<StockItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const [sectors, setSectors] = useState<{name: string; code: string}[]>([]);
  const [filters, setFilters] = useState({
    minPrice: "1", maxPrice: "5",
    minChangePct: "", maxChangePct: "",
    minTurnoverRate: "", minAmount: "",
    minPe: "", maxPe: "",
    minPb: "", maxPb: "",
    minMktcap: "", maxMktcap: "",
    minNmc: "", maxNmc: "",
    minAmplitude: "", maxAmplitude: "",
    minVolumeRatio: "", maxVolumeRatio: "",
    near52High: false, near52Low: false,
    sector: "",
    stFilter: "all" as "all" | "exclude" | "only",
    keyword: "",
    sortBy: "涨跌幅", sortOrder: "desc",
  });

  useEffect(() => { api.getSectors().then(setSectors).catch(() => {}); }, []);

  const buildParams = useCallback(() => {
    const p: Record<string, string | number | boolean> = {
      min_price: filters.minPrice || 1,
      max_price: filters.maxPrice || 5,
      sort_by: filters.sortBy,
      sort_order: filters.sortOrder,
      page, page_size: 20,
    };
    const opt: [string, string][] = [
      ["min_change_pct", filters.minChangePct], ["max_change_pct", filters.maxChangePct],
      ["min_turnover_rate", filters.minTurnoverRate], ["min_amount", filters.minAmount],
      ["min_pe", filters.minPe], ["max_pe", filters.maxPe],
      ["min_pb", filters.minPb], ["max_pb", filters.maxPb],
      ["min_amplitude", filters.minAmplitude], ["max_amplitude", filters.maxAmplitude],
      ["min_volume_ratio", filters.minVolumeRatio], ["max_volume_ratio", filters.maxVolumeRatio],
    ];
    for (const [k, v] of opt) {
      if (v) p[k] = v;
    }
    if (filters.minMktcap) p.min_mktcap = parseFloat(filters.minMktcap) * 1e8;
    if (filters.maxMktcap) p.max_mktcap = parseFloat(filters.maxMktcap) * 1e8;
    if (filters.minNmc) p.min_nmc = parseFloat(filters.minNmc) * 1e8;
    if (filters.maxNmc) p.max_nmc = parseFloat(filters.maxNmc) * 1e8;
    if (filters.minAmount) p.min_amount = parseFloat(filters.minAmount) * 1e8;
    if (filters.stFilter === "exclude") p.exclude_st = true;
    if (filters.stFilter === "only") p.only_st = true;
    if (filters.keyword) p.keyword = filters.keyword;
    if (filters.near52High) p.near_52week_high = true;
    if (filters.near52Low) p.near_52week_low = true;
    if (filters.sector) p.sector = filters.sector;
    return p;
  }, [filters, page]);

  const fetchStocks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getSpot(buildParams());
      setStocks(res.items);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  }, [buildParams]);

  useEffect(() => { fetchStocks(); }, [fetchStocks]);

  const totalPages = Math.ceil(total / 20);

  const f = (key: string, val: string | boolean) => { setFilters({ ...filters, [key]: val }); setPage(1); };

  return (
    <div className="market-tab">
      <div className="filters">
        <label>价格<input type="number" value={filters.minPrice} placeholder="最低" onChange={(e) => f("minPrice", e.target.value)} />-<input type="number" value={filters.maxPrice} placeholder="最高" onChange={(e) => f("maxPrice", e.target.value)} /></label>
        <label>涨跌幅%<input type="number" value={filters.minChangePct} placeholder="最低" onChange={(e) => f("minChangePct", e.target.value)} />-<input type="number" value={filters.maxChangePct} placeholder="最高" onChange={(e) => f("maxChangePct", e.target.value)} /></label>
        <label>ST<select value={filters.stFilter} onChange={(e) => f("stFilter", e.target.value)}>
          <option value="all">全部</option><option value="exclude">排除ST</option><option value="only">仅ST</option>
        </select></label>
        <label>行业<select value={filters.sector} onChange={(e) => f("sector", e.target.value)}>
          <option value="">全部</option>
          {sectors.map((s) => <option key={s.code} value={s.name}>{s.name}</option>)}
        </select></label>
        <label>搜索<input type="text" value={filters.keyword} placeholder="代码/名称" onChange={(e) => f("keyword", e.target.value)} /></label>
        <button onClick={fetchStocks}>筛选</button>
        <button className="toggle-more" onClick={() => setShowMore(!showMore)}>{showMore ? "收起" : "更多"}</button>
      </div>
      {showMore && (
        <div className="filters more-filters">
          <label>换手率%≥<input type="number" value={filters.minTurnoverRate} onChange={(e) => f("minTurnoverRate", e.target.value)} /></label>
          <label>成交额≥(亿)<input type="number" value={filters.minAmount} onChange={(e) => f("minAmount", e.target.value)} /></label>
          <label>市盈率<input type="number" value={filters.minPe} placeholder="最低" onChange={(e) => f("minPe", e.target.value)} />-<input type="number" value={filters.maxPe} placeholder="最高" onChange={(e) => f("maxPe", e.target.value)} /></label>
          <label>市净率<input type="number" value={filters.minPb} placeholder="最低" onChange={(e) => f("minPb", e.target.value)} />-<input type="number" value={filters.maxPb} placeholder="最高" onChange={(e) => f("maxPb", e.target.value)} /></label>
          <label>总市值(亿)<input type="number" value={filters.minMktcap} placeholder="最低" onChange={(e) => f("minMktcap", e.target.value)} />-<input type="number" value={filters.maxMktcap} placeholder="最高" onChange={(e) => f("maxMktcap", e.target.value)} /></label>
          <label>流通市值(亿)<input type="number" value={filters.minNmc} placeholder="最低" onChange={(e) => f("minNmc", e.target.value)} />-<input type="number" value={filters.maxNmc} placeholder="最高" onChange={(e) => f("maxNmc", e.target.value)} /></label>
          <label>振幅%<input type="number" value={filters.minAmplitude} placeholder="最低" onChange={(e) => f("minAmplitude", e.target.value)} />-<input type="number" value={filters.maxAmplitude} placeholder="最高" onChange={(e) => f("maxAmplitude", e.target.value)} /></label>
          <label>量比<input type="number" value={filters.minVolumeRatio} placeholder="最低" onChange={(e) => f("minVolumeRatio", e.target.value)} />-<input type="number" value={filters.maxVolumeRatio} placeholder="最高" onChange={(e) => f("maxVolumeRatio", e.target.value)} /></label>
          <label className="checkbox-label"><input type="checkbox" checked={filters.near52High} onChange={(e) => f("near52High", e.target.checked)} />52周新高</label>
          <label className="checkbox-label"><input type="checkbox" checked={filters.near52Low} onChange={(e) => f("near52Low", e.target.checked)} />52周新低</label>
        </div>
      )}
      <div className="filters sort-bar">
        <label>排序<select value={filters.sortBy} onChange={(e) => { setFilters({ ...filters, sortBy: e.target.value }); setPage(1); }}>
          <option value="涨跌幅">涨跌幅</option><option value="换手率">换手率</option>
          <option value="成交量">成交量</option><option value="最新价">价格</option>
          <option value="成交额">成交额</option><option value="市盈率-动态">市盈率</option>
          <option value="市净率">市净率</option><option value="总市值">总市值</option>
          <option value="量比">量比</option>
        </select></label>
        <label>方向<select value={filters.sortOrder} onChange={(e) => { setFilters({ ...filters, sortOrder: e.target.value }); setPage(1); }}>
          <option value="desc">降序</option><option value="asc">升序</option>
        </select></label>
        <span className="result-info">共 {total} 只</span>
      </div>
      {loading ? <div className="loading">加载中...</div> : (
        <table className="stock-table">
          <thead>
            <tr><th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>换手率</th><th>成交量</th><th>操作</th></tr>
          </thead>
          <tbody>
            {stocks.map((s) => (
              <tr key={s["代码"]}>
                <td><button className="stock-link" onClick={() => onSelectStock(s["代码"])}>{s["代码"]}</button></td>
                <td><button className="stock-link" onClick={() => onSelectStock(s["代码"])}>{s["名称"]}</button></td>
                <td className="price">{s["最新价"]}</td>
                <td className={s["涨跌幅"] >= 0 ? "profit" : "loss"}>{s["涨跌幅"].toFixed(2)}%</td>
                <td>{s["换手率"]?.toFixed(2)}%</td>
                <td>{(s["成交量"] / 10000).toFixed(0)}万</td>
                <td><TradeButton code={s["代码"]} name={s["名称"]} price={s["最新价"]} onDone={onTrade} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="pagination">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
        <span>{page} / {totalPages || 1}</span>
        <button disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</button>
      </div>
    </div>
  );
}

function TradeButton({ code, name, price, onDone }: { code: string; name: string; price: number; onDone: () => void }) {
  const { isTradingTime, tradingStatus } = useTradingTime();
  const [open, setOpen] = useState(false);
  const [qty, setQty] = useState(100);
  const [action, setAction] = useState<"buy" | "sell">("buy");

  const execute = async () => {
    const fn = action === "buy" ? api.buy : api.sell;
    await fn(code, name, qty);
    setOpen(false);
    setQty(100);
    onDone();
  };

  if (!open) {
    return <button className="trade-btn" disabled={!isTradingTime} onClick={() => setOpen(true)} title={!isTradingTime ? tradingStatus : undefined}>交易</button>;
  }
  return (
    <div className="trade-panel">
      <div className="trade-price">当前价: ¥{price}</div>
      <div className="trade-row">
        <button className={action === "buy" ? "active" : ""} onClick={() => setAction("buy")}>买入</button>
        <button className={action === "sell" ? "active" : ""} onClick={() => setAction("sell")}>卖出</button>
      </div>
      <div className="trade-row">
        <label>数量(股)</label>
        <input type="number" value={qty} step={100} min={100} onChange={(e) => setQty(Math.max(100, Math.round(Number(e.target.value) / 100) * 100))} />
      </div>
      <div className="trade-row">
        <span>金额: ¥{(qty * price).toFixed(2)}</span>
      </div>
      <div className="trade-actions">
        <button className="confirm" onClick={execute}>确认{action === "buy" ? "买入" : "卖出"}</button>
        <button onClick={() => setOpen(false)}>取消</button>
      </div>
    </div>
  );
}

function PositionsTab({ positions, onTrade, onSelectStock }: { positions: Position[]; onTrade: () => void; onSelectStock: (code: string) => void }) {
  if (positions.length === 0) return <div className="empty">暂无持仓</div>;
  return (
    <table className="stock-table">
      <thead>
        <tr><th>代码</th><th>名称</th><th>持仓</th><th>成本</th><th>现价</th><th>盈亏</th><th>盈亏%</th><th>操作</th></tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.code}>
            <td><button className="stock-link" onClick={() => onSelectStock(p.code)}>{p.code}</button></td>
            <td><button className="stock-link" onClick={() => onSelectStock(p.code)}>{p.name}</button></td>
            <td>{p.quantity}</td>
            <td>{p.avg_cost.toFixed(3)}</td>
            <td className="price">{p.current_price.toFixed(3)}</td>
            <td className={p.profit >= 0 ? "profit" : "loss"}>{p.profit >= 0 ? "+" : ""}{p.profit.toFixed(2)}</td>
            <td className={p.profit_pct >= 0 ? "profit" : "loss"}>{p.profit_pct.toFixed(2)}%</td>
            <td><TradeButton code={p.code} name={p.name} price={p.current_price} onDone={onTrade} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TransactionsTab({ transactions, onFilter }: { transactions: Transaction[]; onFilter: (start: string, end: string, action: string) => void }) {
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [actionFilter, setActionFilter] = useState("");

  const apply = () => onFilter(startDate, endDate, actionFilter);

  if (transactions.length === 0) return (
    <div>
      <div className="filters">
        <label>起始日期<input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label>
        <label>截止日期<input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></label>
        <label>操作<select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
          <option value="">全部</option>
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select></label>
        <button onClick={apply}>筛选</button>
      </div>
      <div className="empty">暂无交易记录</div>
    </div>
  );

  return (
    <div>
      <div className="filters">
        <label>起始日期<input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label>
        <label>截止日期<input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></label>
        <label>操作<select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
          <option value="">全部</option>
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select></label>
        <button onClick={apply}>筛选</button>
        {(startDate || endDate || actionFilter) && <button onClick={() => { setStartDate(""); setEndDate(""); setActionFilter(""); onFilter("", "", ""); }}>清除</button>}
      </div>
      <table className="stock-table">
        <thead>
          <tr><th>时间</th><th>操作</th><th>代码</th><th>名称</th><th>数量</th><th>价格</th><th>金额</th></tr>
        </thead>
        <tbody>
          {transactions.map((t) => (
            <tr key={t.id}>
              <td>{new Date(t.created_at * 1000).toLocaleString()}</td>
              <td className={t.action === "buy" ? "buy-label" : "sell-label"}>{t.action === "buy" ? "买入" : "卖出"}</td>
              <td>{t.code}</td>
              <td>{t.name}</td>
              <td>{t.quantity}</td>
              <td>{t.price.toFixed(3)}</td>
              <td>¥{t.amount.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ============ Stock Detail Page ============

function StockDetail({ code, positions, onBack, onTrade }: {
  code: string;
  positions: Position[];
  onBack: () => void;
  onTrade: () => void;
}) {
  const { isTradingTime, tradingStatus } = useTradingTime();
  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [klinePeriod, setKlinePeriod] = useState<"daily" | "weekly" | "monthly">("daily");
  const chartRef = useRef<HTMLDivElement>(null);
  const chartApiRef = useRef<IChartApi | null>(null);
  const [tradeAction, setTradeAction] = useState<"buy" | "sell">("buy");
  const [tradeQty, setTradeQty] = useState(100);

  const pos = positions.find((p) => p.code === code);

  useEffect(() => {
    api.getDetail(code).then(setDetail);
  }, [code]);

  useEffect(() => {
    const container = chartRef.current;
    if (!container) return;
    if (chartApiRef.current) {
      chartApiRef.current.remove();
      chartApiRef.current = null;
    }

    // 等容器布局完成后再创建图表
    const raf = requestAnimationFrame(() => {
      if (!container) return;
      const chart = createChart(container, {
        width: container.clientWidth || 800,
        height: 320,
        layout: {
          background: { type: ColorType.Solid, color: "#161b22" },
          textColor: "#c9d1d9",
        },
        grid: {
          vertLines: { color: "#21262d" },
          horzLines: { color: "#21262d" },
        },
        timeScale: { borderColor: "#30363d" },
      });
      chartApiRef.current = chart;

      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: "#f85149",
        downColor: "#3fb950",
        borderUpColor: "#f85149",
        borderDownColor: "#3fb950",
        wickUpColor: "#f85149",
        wickDownColor: "#3fb950",
      });

      const volumeSeries = chart.addSeries(HistogramSeries, {
        color: "#58a6ff",
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });

      api.getHistory(code, klinePeriod).then((data) => {
        if (!data.length) return;
        const candleData: CandlestickData[] = data.map((d) => ({
          time: d.day,
          open: parseFloat(d.open),
          high: parseFloat(d.high),
          low: parseFloat(d.low),
          close: parseFloat(d.close),
        }));
        const volumeData: HistogramData[] = data.map((d) => ({
          time: d.day,
          value: parseFloat(d.volume),
          color: parseFloat(d.close) >= parseFloat(d.open) ? "rgba(248,81,73,0.3)" : "rgba(63,185,80,0.3)",
        }));
        candleSeries.setData(candleData);
        volumeSeries.setData(volumeData);
        chart.timeScale().fitContent();
      });
    });

    const handleResize = () => {
      if (chartRef.current && chartApiRef.current) {
        chartApiRef.current.applyOptions({ width: chartRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", handleResize);
      if (chartApiRef.current) {
        chartApiRef.current.remove();
        chartApiRef.current = null;
      }
    };
  }, [code, klinePeriod]);

  if (!detail) return <div className="loading">加载中...</div>;

  const price = detail["最新价"];
  const changeClass = detail["涨跌幅"] >= 0 ? "profit" : "loss";
  const fmt = (v: number, decimals = 2) => (v ? v.toFixed(decimals) : "-");
  const fmtCap = (v: number) => {
    if (!v) return "-";
    if (v >= 1e12) return (v / 1e12).toFixed(2) + "万亿";
    if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
    if (v >= 1e4) return (v / 1e4).toFixed(2) + "万";
    return v.toFixed(0);
  };

  const executeTrade = async () => {
    if (tradeAction === "buy") {
      await api.buy(code, detail["名称"], tradeQty);
    } else {
      await api.sell(code, tradeQty);
    }
    setTradeQty(100);
    onTrade();
    const updated = await api.getDetail(code);
    setDetail(updated);
  };

  return (
    <div className="app stock-detail">
      {/* Header */}
      <div className="detail-header">
        <button className="back-btn" onClick={onBack}>← 返回</button>
        <div className="detail-title">
          <span className="detail-name">{detail["名称"]}</span>
          <span className="detail-code">{detail["代码"]}</span>
        </div>
      </div>
      <div className="detail-price-row">
        <span className={`detail-price ${changeClass}`}>¥{fmt(price)}</span>
        <span className={changeClass}>{detail["涨跌幅"] >= 0 ? "+" : ""}{fmt(detail["涨跌额"])}</span>
        <span className={changeClass}>{detail["涨跌幅"] >= 0 ? "+" : ""}{fmt(detail["涨跌幅"])}%</span>
      </div>

      {/* Metrics grid */}
      <div className="detail-metrics">
        <div className="metric"><span className="metric-label">今开</span><span className="metric-value">{fmt(detail["今开"])}</span></div>
        <div className="metric"><span className="metric-label">最高</span><span className="metric-value profit">{fmt(detail["最高"])}</span></div>
        <div className="metric"><span className="metric-label">最低</span><span className="metric-value loss">{fmt(detail["最低"])}</span></div>
        <div className="metric"><span className="metric-label">昨收</span><span className="metric-value">{fmt(detail["昨收"])}</span></div>
        <div className="metric"><span className="metric-label">买一</span><span className="metric-value">{fmt(detail["买一"])}</span></div>
        <div className="metric"><span className="metric-label">卖一</span><span className="metric-value">{fmt(detail["卖一"])}</span></div>
        <div className="metric"><span className="metric-label">成交量</span><span className="metric-value">{detail["成交量"] ? (detail["成交量"] / 10000).toFixed(0) + "万" : "-"}</span></div>
        <div className="metric"><span className="metric-label">成交额</span><span className="metric-value">{fmtCap(detail["成交额"])}</span></div>
        <div className="metric"><span className="metric-label">换手率</span><span className="metric-value">{fmt(detail["换手率"])}%</span></div>
        <div className="metric"><span className="metric-label">市盈率</span><span className="metric-value">{fmt(detail["市盈率-动态"])}</span></div>
        <div className="metric"><span className="metric-label">市净率</span><span className="metric-value">{fmt(detail["市净率"])}</span></div>
        <div className="metric"><span className="metric-label">总市值</span><span className="metric-value">{fmtCap(detail["总市值"])}</span></div>
        <div className="metric"><span className="metric-label">流通市值</span><span className="metric-value">{fmtCap(detail["流通市值"])}</span></div>
      </div>

      {/* K-line chart */}
      <div className="detail-chart-section">
        <div className="period-tabs">
          {(["daily", "weekly", "monthly"] as const).map((p) => (
            <button key={p} className={klinePeriod === p ? "tab active" : "tab"} onClick={() => setKlinePeriod(p)}>
              {p === "daily" ? "日K" : p === "weekly" ? "周K" : "月K"}
            </button>
          ))}
        </div>
        <div ref={chartRef} className="chart-container" />
      </div>

      {/* Position info */}
      {pos && (
        <div className="detail-position">
          <span>持仓: <strong>{pos.quantity}股</strong></span>
          <span>成本: <strong>¥{pos.avg_cost.toFixed(3)}</strong></span>
          <span>盈亏: <strong className={pos.profit >= 0 ? "profit" : "loss"}>{pos.profit >= 0 ? "+" : ""}¥{pos.profit.toFixed(2)} ({pos.profit_pct.toFixed(2)}%)</strong></span>
        </div>
      )}

      {/* Trade panel */}
      <div className="detail-trade">
        {!isTradingTime && <div className="trading-status">{tradingStatus}，无法交易</div>}
        <div className="detail-trade-row">
          <button className={tradeAction === "buy" ? "detail-buy active" : "detail-buy"} onClick={() => setTradeAction("buy")} disabled={!isTradingTime}>买入</button>
          <button className={tradeAction === "sell" ? "detail-sell active" : "detail-sell"} onClick={() => setTradeAction("sell")} disabled={!isTradingTime}>卖出</button>
        </div>
        <div className="detail-trade-row">
          <span>当前价: ¥{fmt(price)}</span>
          <label>数量(股)<input type="number" value={tradeQty} step={100} min={100} onChange={(e) => setTradeQty(Math.max(100, Math.round(Number(e.target.value) / 100) * 100))} /></label>
          <span>金额: ¥{(tradeQty * price).toFixed(2)}</span>
        </div>
        <button className="detail-confirm" onClick={executeTrade} disabled={!isTradingTime}>确认{tradeAction === "buy" ? "买入" : "卖出"}</button>
      </div>
    </div>
  );
}
