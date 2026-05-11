import { useState, useEffect, useCallback, useRef } from "react";
import { api, type StockItem, type StockDetail as StockDetailType, type AccountInfo, type Position, type Transaction, type SectorOverviewItem, type WatchlistItem, type WatchlistGroup, type LhbItem, type DailySnapshot, type PerformanceStats, type PendingOrder, type EtfItem } from "./api";
import { createChart, LineSeries, type IChartApi, ColorType } from "lightweight-charts";
import { toast, Toast, ToastProvider, usePolling, TradingTimeProvider, useTradingTime } from "./utils/shared";
import { fmtAmt } from "./utils/format";
import StockDetail from "./components/StockDetail";
import "./App.css";


// 可搜索下拉组件
function SearchSelect({ value, onChange, options, placeholder }: {
  value: string;
  onChange: (val: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const filtered = search
    ? options.filter((o) => o.label.toLowerCase().includes(search.toLowerCase()) || o.value.toLowerCase().includes(search.toLowerCase()))
    : options;

  const selected = options.find((o) => o.value === value);

  return (
    <div className={`search-select${open ? " open" : ""}${value ? " has-value" : ""}`} ref={ref}>
      <button className="search-select-trigger" onClick={() => { setOpen(!open); setSearch(""); }}>
        <span className={!value ? "placeholder" : ""}>{selected?.label || placeholder || "请选择"}</span>
        <span className="search-select-arrow">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="search-select-dropdown">
          <input
            className="search-select-input"
            autoFocus
            placeholder="搜索..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="search-select-list">
            <button className="search-select-option" onClick={() => { onChange(""); setOpen(false); }}>
              {placeholder || "全部"}
            </button>
            {filtered.map((o) => (
              <button key={o.value} className={`search-select-option${o.value === value ? " active" : ""}`}
                onClick={() => { onChange(o.value); setOpen(false); }}>
                {o.label}
              </button>
            ))}
            {filtered.length === 0 && <div className="search-select-empty">无匹配</div>}
          </div>
        </div>
      )}
    </div>
  );
}
type Tab = "market" | "etf" | "watchlist" | "sectors" | "ranking" | "positions" | "orders" | "analysis" | "transactions";

export default function App() {
  return (
    <TradingTimeProvider>
      <ToastProvider>
        <AppInner />
        <Toast />
      </ToastProvider>
    </TradingTimeProvider>
  );
}

function ComparePanel({ list, setList, onSelectStock }: {
  list: { code: string; name: string }[];
  setList: React.Dispatch<React.SetStateAction<{ code: string; name: string }[]>>;
  onSelectStock: (code: string) => void;
}) {
  const [details, setDetails] = useState<StockDetailType[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (list.length === 0) return;
    setLoading(true);
    Promise.all(list.map((s) => api.getDetail(s.code)))
      .then((results) => setDetails(results.filter(Boolean)))
      .finally(() => setLoading(false));
  }, [list]);

  const removeFromCompare = (code: string) => {
    setList((prev) => prev.filter((c) => c.code !== code));
  };

  if (list.length === 0) return null;

  const metrics: { label: string; key: keyof StockDetailType; fmt: (v: number) => string }[] = [
    { label: "最新价", key: "最新价", fmt: (v) => v.toFixed(2) },
    { label: "涨跌幅", key: "涨跌幅", fmt: (v) => (v >= 0 ? "+" : "") + v.toFixed(2) + "%" },
    { label: "涨跌额", key: "涨跌额", fmt: (v) => (v >= 0 ? "+" : "") + v.toFixed(2) },
    { label: "换手率", key: "换手率", fmt: (v) => v.toFixed(2) + "%" },
    { label: "市盈率", key: "市盈率-动态", fmt: (v) => v.toFixed(1) },
    { label: "市净率", key: "市净率", fmt: (v) => v.toFixed(2) },
    { label: "量比", key: "量比", fmt: (v) => v.toFixed(2) },
    { label: "成交额", key: "成交额", fmt: (v) => v >= 1e8 ? (v / 1e8).toFixed(1) + "亿" : (v / 1e4).toFixed(0) + "万" },
  ];

  return (
    <div className="compare-panel">
      <div className="compare-header">
        <span>多股对比 ({list.length}/5)</span>
        <button className="compare-close" onClick={() => setList([])}>关闭</button>
      </div>
      {loading ? <div className="loading">加载中...</div> : (
        <div className="compare-table-wrap">
          <table className="compare-table">
            <thead>
              <tr>
                <th>指标</th>
                {details.map((d) => (
                  <th key={d.代码}>
                    <button className="stock-link" onClick={() => onSelectStock(d.代码)}>{d.名称}</button>
                    <span className="compare-remove" onClick={() => removeFromCompare(d.代码)}>x</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => (
                <tr key={m.label}>
                  <td className="compare-metric">{m.label}</td>
                  {details.map((d) => {
                    const val = d[m.key] as number;
                    const isChange = m.key === "涨跌幅" || m.key === "涨跌额";
                    return (
                      <td key={d.代码 + m.key} className={isChange ? (val >= 0 ? "profit" : "loss") : ""}>
                        {val != null ? m.fmt(val) : "-"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AppInner() {
  const [tab, setTab] = useState<Tab>("market");
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [txFilter, setTxFilter] = useState<{ start_date?: string; end_date?: string; action?: string }>({});
  const [selectedStock, setSelectedStock] = useState<string | null>(null);
  const [compareList, setCompareList] = useState<{ code: string; name: string }[]>([]);
  const [showCompare, setShowCompare] = useState(false);
  const [pendingFilter, setPendingFilter] = useState<{sector?: string; keyword?: string} | null>(null);

  const refresh = useCallback(async () => {
    const [dash, txs] = await Promise.all([
      api.getDashboard(),
      api.getTransactions({ limit: 200, ...txFilter }),
    ]);
    setAccount(dash.account);
    setPositions(dash.positions);
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

  const isEtfCode = selectedStock ? /^[15]/.test(selectedStock) : false;

  if (selectedStock) {
    return (
      <StockDetail
        code={selectedStock}
        positions={positions}
        onBack={() => setSelectedStock(null)}
        isEtf={isEtfCode}
        onTrade={refresh}
        onAddCompare={(code, name) => {
          if (compareList.length >= 5) { toast("最多对比5只股票"); return; }
          if (!compareList.find((c) => c.code === code)) {
            setCompareList([...compareList, { code, name }]);
          }
        }}
      />
    );
  }

  const { isTradingTime, tradingStatus, sessions } = useTradingTime();

  return (
    <div className="app">
      <header className="header">
        <h1>A股低价股模拟炒股</h1>
        <div className="market-status-bar">
          <span className={`market-status ${isTradingTime ? "open" : "closed"}`}>{tradingStatus}</span>
          {sessions.length > 0 && (
            <span className="market-sessions">交易时间：{sessions.map((s) => `${s.start}-${s.end}`).join(" / ")}</span>
          )}
        </div>
        {account && <AccountBar account={account} />}
        {compareList.length > 0 && (
          <button className="compare-badge" onClick={() => setShowCompare(!showCompare)}>
            对比({compareList.length})
          </button>
        )}
        <NotificationBell />
      </header>
      <nav className="tabs">
        {(["market", "etf", "watchlist", "sectors", "ranking", "positions", "orders", "analysis", "transactions"] as Tab[]).map((t) => (
          <button key={t} className={`tab${tab === t ? " active" : ""}`} data-tab={t} onClick={() => setTab(t)}>
            <span className="tab-icon">{t === "market" ? "📊" : t === "etf" ? "🏦" : t === "watchlist" ? "⭐" : t === "sectors" ? "🏭" : t === "ranking" ? "🏆" : t === "positions" ? "💰" : t === "orders" ? "📋" : t === "analysis" ? "📈" : "📒"}</span>
            <span className="tab-label">{t === "market" ? "行情" : t === "etf" ? "ETF" : t === "watchlist" ? "自选" : t === "sectors" ? "板块" : t === "ranking" ? "排行" : t === "positions" ? "持仓" : t === "orders" ? "委托" : t === "analysis" ? "分析" : "记录"}</span>
          </button>
        ))}
        <button className="tab reset" onClick={() => { if (confirm("确定重置账户？所有持仓和交易记录将被清除！")) { api.reset().then(refresh); } }}>重置</button>
      </nav>
      <main className="main">
        {tab === "market" && <MarketTab onTrade={refresh} onSelectStock={setSelectedStock} pendingFilter={pendingFilter} onFilterApplied={() => setPendingFilter(null)} />}
        {tab === "etf" && <EtfTab onTrade={refresh} onSelectStock={setSelectedStock} />}
        {tab === "watchlist" && <WatchlistTab onSelectStock={setSelectedStock} onTrade={refresh} />}
        {tab === "sectors" && <SectorsTab onSelectSector={(f) => { setTab("market"); setPendingFilter(f); }} onSelectStock={setSelectedStock} />}
        {tab === "ranking" && <RankingTab onSelectStock={setSelectedStock} />}
        {tab === "positions" && <PositionsTab positions={positions} onTrade={refresh} onSelectStock={setSelectedStock} />}
        {tab === "orders" && <OrdersTab onTrade={refresh} />}
        {tab === "analysis" && <AnalysisTab />}
        {tab === "transactions" && <TransactionsTab transactions={transactions} onFilter={handleTxFilter} />}
      </main>
      {showCompare && <ComparePanel list={compareList} setList={setCompareList} onSelectStock={setSelectedStock} />}
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

function NotificationBell() {
  const [triggeredAlerts, setTriggeredAlerts] = useState<{ id: number; code: string; name: string; message: string }[]>([]);
  const [showPanel, setShowPanel] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const alerts = await api.getAlerts("triggered");
    setTriggeredAlerts(alerts.map((a: any) => ({ id: a.id, code: a.code, name: a.name, message: a.message || "" })));
  }, []);

  usePolling(load, 60000);

  useEffect(() => {
    if (!showPanel) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setShowPanel(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showPanel]);

  const hasNew = triggeredAlerts.length > 0;

  return (
    <div className="notification-bell" ref={ref} onClick={() => setShowPanel(!showPanel)}>
      <span className="bell-icon">🔔</span>
      {hasNew && <span className="bell-badge">{triggeredAlerts.length}</span>}
      {showPanel && (
        <div className="notification-panel">
          <h4>提醒通知</h4>
          {triggeredAlerts.length === 0 ? (
            <div className="empty">暂无触发提醒</div>
          ) : (
            <ul>
              {triggeredAlerts.map((a) => (
                <li key={a.id}><strong>{a.name}</strong> {a.message}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function MarketTab({ onTrade, onSelectStock, pendingFilter, onFilterApplied }: { onTrade: () => void; onSelectStock: (code: string) => void; pendingFilter?: {sector?: string; keyword?: string} | null; onFilterApplied?: () => void }) {
  const [stocks, setStocks] = useState<StockItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
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

  useEffect(() => {
    if (!pendingFilter) return;
    setFilters((f) => ({
      ...f,
      ...(pendingFilter.sector != null ? { sector: pendingFilter.sector } : {}),
      ...(pendingFilter.keyword != null ? { keyword: pendingFilter.keyword } : {}),
    }));
    setPage(1);
    onFilterApplied?.();
  }, [pendingFilter, onFilterApplied]);

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
    setError(null);
    try {
      const res = await api.getSpot(buildParams() as Record<string, string | number>);
      setStocks(res.items);
      setTotal(res.total);
      setWarning(res.warning || null);
    } catch (e: any) {
      setWarning("数据刷新失败，当前显示的可能不是最新数据");
    } finally {
      setLoading(false);
    }
  }, [buildParams]);

  // 防抖：筛选条件变化后400ms才触发请求
  useEffect(() => {
    const t = setTimeout(fetchStocks, 400);
    return () => clearTimeout(t);
  }, [fetchStocks]);

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
        <label>板块<SearchSelect value={filters.sector} onChange={(v) => f("sector", v)} placeholder="全部板块" options={sectors.map((s) => ({ value: s.name, label: s.name }))} /></label>
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
      {warning && <div className="warning-bar">{warning}</div>}
      {loading ? <div className="loading">加载中...</div> : (
        <div className="table-wrap"><table className="stock-table">
          <thead>
            <tr><th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>换手率</th><th>成交量</th><th>操作</th></tr>
          </thead>
          <tbody>
            {stocks.length === 0 && !loading && error && (
              <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--loss-color)", padding: "24px" }}>{error}</td></tr>
            )}
            {stocks.length === 0 && !loading && !error && (
              <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--text-secondary)", padding: "24px" }}>暂无符合筛选条件的股票</td></tr>
            )}
            {stocks.map((s) => (
              <tr key={s["代码"]}>
                <td><button className="stock-link" onClick={() => onSelectStock(s["代码"])}>{s["代码"]}</button></td>
                <td><button className="stock-link" onClick={() => onSelectStock(s["代码"])}>{s["名称"]}</button></td>
                <td className="price">{s["最新价"]}</td>
                <td className={s["涨跌幅"] >= 0 ? "profit" : "loss"}>{s["涨跌幅"].toFixed(2)}%</td>
                <td>{s["换手率"]?.toFixed(2)}%</td>
                <td>{(s["成交量"] / 10000).toFixed(0)}万</td>
                <td>
                  <button className="watch-btn" onClick={async () => { await api.addWatchlist(s["代码"], s["名称"]); toast("已加自选"); }}>自选</button>
                  <TradeButton code={s["代码"]} name={s["名称"]} price={s["最新价"]} onDone={onTrade} />
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
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
  const [submitting, setSubmitting] = useState(false);

  const execute = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const fn = action === "buy" ? api.buy : api.sell as any;
      await fn(code, name, qty);
      setOpen(false);
      setQty(100);
      onDone();
    } catch (e: any) {
      toast(e?.detail || e?.message || "交易失败");
    } finally {
      setSubmitting(false);
    }
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
        <button className="confirm" onClick={execute} disabled={submitting}>{submitting ? "处理中..." : `确认${action === "buy" ? "买入" : "卖出"}`}</button>
        <button onClick={() => setOpen(false)}>取消</button>
      </div>
    </div>
  );
}

function PositionsTab({ positions, onTrade, onSelectStock }: { positions: Position[]; onTrade: () => void; onSelectStock: (code: string) => void }) {
  if (positions.length === 0) return <div className="empty">暂无持仓</div>;
  return (
    <div className="table-wrap"><table className="stock-table">
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
    </table></div>
  );
}

function WatchlistTab({ onSelectStock, onTrade }: { onSelectStock: (code: string) => void; onTrade: () => void }) {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [groups, setGroups] = useState<WatchlistGroup[]>([]);
  const [activeGroup, setActiveGroup] = useState<number>(0); // 0 = all
  const [loading, setLoading] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");

  const fetchList = useCallback(() => {
    setLoading(true);
    const gid = activeGroup || undefined;
    api.getWatchlist(gid).then(setItems).finally(() => setLoading(false));
  }, [activeGroup]);

  const fetchGroups = useCallback(() => {
    api.getGroups().then(setGroups);
  }, []);

  useEffect(() => { fetchGroups(); }, [fetchGroups]);
  usePolling(fetchList, 30000, [fetchList]);

  const handleRemove = async (code: string) => {
    await api.removeWatchlist(code);
    toast("已移除");
    fetchList();
  };

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;
    await api.createGroup(newGroupName.trim());
    toast("分组已创建");
    setNewGroupName("");
    fetchGroups();
  };

  const handleDeleteGroup = async (id: number) => {
    await api.deleteGroup(id);
    toast("分组已删除");
    if (activeGroup === id) setActiveGroup(0);
    fetchGroups();
    fetchList();
  };

  const handleMove = async (code: string, groupId: number) => {
    await api.moveWatchlist(code, groupId);
    fetchList();
  };

  if (loading && items.length === 0) return <div className="loading">加载中...</div>;

  return (
    <div>
      {/* 分组栏 */}
      <div className="group-bar">
        <button className={activeGroup === 0 ? "tab active" : "tab"} onClick={() => setActiveGroup(0)}>全部</button>
        {groups.map((g) => (
          <button key={g.id} className={activeGroup === g.id ? "tab active" : "tab"} onClick={() => setActiveGroup(g.id)}>
            {g.name}
            {g.id !== 1 && <span className="group-del" onClick={(e) => { e.stopPropagation(); handleDeleteGroup(g.id); }}>x</span>}
          </button>
        ))}
        <input className="group-input" placeholder="新分组" value={newGroupName} onChange={(e) => setNewGroupName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreateGroup()} />
        <button className="tab" onClick={handleCreateGroup}>+</button>
      </div>

      {items.length === 0 ? <div className="empty">暂无自选股，在行情页点击"自选"添加</div> : (
        <div className="table-wrap"><table className="stock-table">
          <thead>
            <tr><th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>涨跌额</th><th>操作</th></tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.code}>
                <td><button className="stock-link" onClick={() => onSelectStock(s.code)}>{s.code}</button></td>
                <td><button className="stock-link" onClick={() => onSelectStock(s.code)}>{s.name}</button></td>
                <td className="price">{s.price.toFixed(2)}</td>
                <td className={s.change_pct >= 0 ? "profit" : "loss"}>{s.change_pct >= 0 ? "+" : ""}{s.change_pct.toFixed(2)}%</td>
                <td className={s.change_amt >= 0 ? "profit" : "loss"}>{s.change_amt >= 0 ? "+" : ""}{s.change_amt.toFixed(2)}</td>
                <td>
                  <TradeButton code={s.code} name={s.name} price={s.price} onDone={onTrade} />
                  <button className="remove-btn" onClick={() => handleRemove(s.code)}>删除</button>
                  {groups.length > 1 && (
                    <SearchSelect value={String(s.group_id)} onChange={(v) => handleMove(s.code, Number(v))} options={groups.map((g) => ({ value: String(g.id), label: g.name }))} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}

function EtfTab({ onTrade, onSelectStock }: { onTrade: () => void; onSelectStock: (code: string) => void }) {
  const [etfs, setEtfs] = useState<EtfItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const [filters, setFilters] = useState({
    minPrice: "", maxPrice: "",
    minChangePct: "", maxChangePct: "",
    minAmount: "", etfType: "", keyword: "",
    sortBy: "涨跌幅", sortOrder: "desc",
  });

  const buildParams = useCallback(() => {
    const p: Record<string, string | number | boolean> = {
      sort_by: filters.sortBy, sort_order: filters.sortOrder,
      page, page_size: 20,
    };
    if (filters.minPrice) p.min_price = filters.minPrice;
    if (filters.maxPrice) p.max_price = filters.maxPrice;
    if (filters.minChangePct) p.min_change_pct = filters.minChangePct;
    if (filters.maxChangePct) p.max_change_pct = filters.maxChangePct;
    if (filters.minAmount) p.min_amount = parseFloat(filters.minAmount) * 1e8;
    if (filters.etfType) p.etf_type = filters.etfType;
    if (filters.keyword) p.keyword = filters.keyword;
    return p;
  }, [filters, page]);

  const fetchEtfs = useCallback(async () => {
    setLoading(true);
    setWarning(null);
    try {
      const res = await api.getEtfSpot(buildParams() as Record<string, string | number>);
      setEtfs(res.items);
      setTotal(res.total);
    } catch {
      setWarning("ETF数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [buildParams]);

  useEffect(() => {
    const t = setTimeout(fetchEtfs, 400);
    return () => clearTimeout(t);
  }, [fetchEtfs]);

  const totalPages = Math.ceil(total / 20);
  const f = (key: string, val: string) => { setFilters({ ...filters, [key]: val }); setPage(1); };

  return (
    <div className="market-tab">
      <div className="filters">
        <label>价格<input type="number" value={filters.minPrice} placeholder="最低" onChange={(e) => f("minPrice", e.target.value)} />-<input type="number" value={filters.maxPrice} placeholder="最高" onChange={(e) => f("maxPrice", e.target.value)} /></label>
        <label>涨跌幅%<input type="number" value={filters.minChangePct} placeholder="最低" onChange={(e) => f("minChangePct", e.target.value)} />-<input type="number" value={filters.maxChangePct} placeholder="最高" onChange={(e) => f("maxChangePct", e.target.value)} /></label>
        <label>类型<select value={filters.etfType} onChange={(e) => f("etfType", e.target.value)}>
          <option value="">全部</option><option value="指数">指数</option><option value="债券">债券</option><option value="商品">商品</option><option value="货币">货币</option><option value="跨境">跨境</option>
        </select></label>
        <label>搜索<input type="text" value={filters.keyword} placeholder="代码/名称" onChange={(e) => f("keyword", e.target.value)} /></label>
        <button onClick={fetchEtfs}>筛选</button>
      </div>
      <div className="filters sort-bar">
        <label>排序<select value={filters.sortBy} onChange={(e) => { setFilters({ ...filters, sortBy: e.target.value }); setPage(1); }}>
          <option value="涨跌幅">涨跌幅</option><option value="最新价">价格</option><option value="成交额">成交额</option><option value="成交量">成交量</option>
        </select></label>
        <label>方向<select value={filters.sortOrder} onChange={(e) => { setFilters({ ...filters, sortOrder: e.target.value }); setPage(1); }}>
          <option value="desc">降序</option><option value="asc">升序</option>
        </select></label>
        <span className="result-info">共 {total} 只</span>
      </div>
      {warning && <div className="warning-bar">{warning}</div>}
      {loading ? <div className="loading">加载中...</div> : (
        <div className="table-wrap"><table className="stock-table">
          <thead>
            <tr><th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>成交额</th><th>操作</th></tr>
          </thead>
          <tbody>
            {etfs.length === 0 && !loading && (
              <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--text-secondary)", padding: "24px" }}>暂无符合筛选条件的ETF</td></tr>
            )}
            {etfs.map((s) => (
              <tr key={s["代码"]}>
                <td><button className="stock-link" onClick={() => onSelectStock(s["代码"])}>{s["代码"]}</button></td>
                <td>{s["名称"]}</td>
                <td>{s["最新价"].toFixed(3)}</td>
                <td className={s["涨跌幅"] >= 0 ? "profit" : "loss"}>{s["涨跌幅"] >= 0 ? "+" : ""}{s["涨跌幅"].toFixed(2)}%</td>
                <td>{fmtAmt(s["成交额"])}</td>
                <td><TradeButton code={s["代码"]} name={s["名称"]} price={s["最新价"]} onDone={onTrade} /></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {totalPages > 1 && (
        <div className="pagination">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
          <span>{page}/{totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}
    </div>
  );
}

function SectorsTab({ onSelectSector, onSelectStock }: { onSelectSector: (filter: {sector?: string; keyword?: string}) => void; onSelectStock: (code: string) => void }) {
  const [sectors, setSectors] = useState<SectorOverviewItem[]>([]);
  const [loading, setLoading] = useState(false);

  const fetchSectors = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getSectorOverview();
      setSectors(data);
    } catch {
      toast("板块数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSectors(); }, [fetchSectors]);
  usePolling(fetchSectors, 120000);

  if (loading) return <div className="loading">加载中...</div>;

  // 主力强度判断
  const getStrengthLabel = (mainNet: number, totalAmount: number): { value: number; label: string; color: string } => {
    if (totalAmount <= 0) return { value: 0, label: "-", color: "" };
    const strength = (mainNet / totalAmount) * 100;
    if (strength <= -1) return { value: strength, label: "出货", color: "loss" };
    if (strength < 1) return { value: strength, label: "洗盘", color: "" };
    if (strength < 3) return { value: strength, label: "建仓", color: "profit" };
    return { value: strength, label: "抢筹", color: "profit" };
  };

  return (
    <div className="table-wrap"><table className="stock-table">
      <thead>
        <tr><th>板块</th><th>涨跌幅</th><th>领涨股</th><th>涨幅</th><th>总金额</th><th>主力</th><th>强度</th></tr>
      </thead>
      <tbody>
        {sectors.map((s) => {
          const strength = getStrengthLabel(s.main_net ?? 0, s.total_amount ?? 0);
          return (
            <tr key={s.name}>
              <td className="sector-name"><button className="stock-link" onClick={() => onSelectSector({sector: s.name})}>{s.name}</button></td>
              <td className={s.avg_change_pct >= 0 ? "profit" : "loss"}>{s.avg_change_pct >= 0 ? "+" : ""}{s.avg_change_pct.toFixed(2)}%</td>
              <td>{s.top_stocks[0] && <button className="stock-link" onClick={() => onSelectStock(s.top_stocks[0].代码)}>{s.top_stocks[0].名称}</button>}</td>
              <td className={s.top_stocks[0]?.涨跌幅 >= 0 ? "profit" : "loss"}>{s.top_stocks[0] ? (s.top_stocks[0].涨跌幅 >= 0 ? "+" : "") + s.top_stocks[0].涨跌幅.toFixed(2) + "%" : "-"}</td>
              <td>{(s.total_amount ?? 0).toFixed(2)}亿</td>
              <td className={(s.main_net ?? 0) >= 0 ? "profit" : "loss"}>{((s.main_net ?? 0) >= 0 ? "+" : "") + (s.main_net ?? 0).toFixed(2) + "亿"}</td>
              <td className={strength.color}>{strength.value.toFixed(1)} {strength.label}</td>
            </tr>
          );
        })}
      </tbody>
    </table></div>
  );
}

type RankingSubTab = "涨幅" | "跌幅" | "换手率" | "成交额" | "量比" | "龙虎榜";

function RankingTab({ onSelectStock }: { onSelectStock: (code: string) => void }) {
  const [subTab, setSubTab] = useState<RankingSubTab>("涨幅");
  const [rankingData, setRankingData] = useState<StockItem[]>([]);
  const [lhbData, setLhbData] = useState<LhbItem[]>([]);
  const [loading, setLoading] = useState(false);

  const rankingSortMap: Record<string, { sortBy: string; order: string }> = {
    "涨幅": { sortBy: "涨跌幅", order: "desc" },
    "跌幅": { sortBy: "涨跌幅", order: "asc" },
    "换手率": { sortBy: "换手率", order: "desc" },
    "成交额": { sortBy: "成交额", order: "desc" },
    "量比": { sortBy: "量比", order: "desc" },
  };

  useEffect(() => {
    if (subTab === "龙虎榜") {
      setLoading(true);
      api.getLhb().then((d) => { setLhbData(Array.isArray(d) ? d : []); }).finally(() => setLoading(false));
    } else {
      const { sortBy, order } = rankingSortMap[subTab];
      setLoading(true);
      api.getRanking(sortBy, order, 50).then((d) => { setRankingData(Array.isArray(d) ? d : []); }).finally(() => setLoading(false));
    }
  }, [subTab]);

  if (loading) return <div className="loading">加载中...</div>;

  return (
    <div className="ranking-tab">
      <div className="detail-main-tabs">
        {(["涨幅", "跌幅", "换手率", "成交额", "量比", "龙虎榜"] as RankingSubTab[]).map((t) => (
          <button key={t} className={`tab${subTab === t ? " active" : ""}`} onClick={() => setSubTab(t)}>{t}</button>
        ))}
      </div>
      {subTab === "龙虎榜" ? (
        <div className="table-wrap"><table className="stock-table lhb-table">
          <thead>
            <tr><th>代码</th><th>名称</th><th>上榜日</th><th>收盘价</th><th>涨跌幅</th><th>净买额</th><th>买入额</th><th>卖出额</th><th>换手率</th><th>上榜原因</th></tr>
          </thead>
          <tbody>
            {lhbData.map((r, i) => (
              <tr key={i}>
                <td><button className="stock-link" onClick={() => onSelectStock(r.代码)}>{r.代码}</button></td>
                <td>{r.名称}</td>
                <td>{r.上榜日}</td>
                <td>{r.收盘价?.toFixed(2)}</td>
                <td className={r.涨跌幅 >= 0 ? "profit" : "loss"}>{r.涨跌幅 >= 0 ? "+" : ""}{r.涨跌幅?.toFixed(2)}%</td>
                <td className={r.净买额 >= 0 ? "profit" : "loss"}>{fmtAmt(r.净买额)}</td>
                <td>{fmtAmt(r.买入额)}</td>
                <td>{fmtAmt(r.卖出额)}</td>
                <td>{r.换手率?.toFixed(2)}%</td>
                <td className="lhb-reason">{r.上榜原因}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      ) : (
        <div className="table-wrap"><table className="stock-table">
          <thead>
            <tr><th>排名</th><th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>换手率</th><th>成交额</th>{subTab === "量比" && <th>量比</th>}</tr>
          </thead>
          <tbody>
            {rankingData.map((s, i) => (
              <tr key={s.代码}>
                <td className="rank-num">{i + 1}</td>
                <td><button className="stock-link" onClick={() => onSelectStock(s.代码)}>{s.代码}</button></td>
                <td>{s.名称}</td>
                <td>{s.最新价?.toFixed(2)}</td>
                <td className={s.涨跌幅 >= 0 ? "profit" : "loss"}>{s.涨跌幅 >= 0 ? "+" : ""}{s.涨跌幅?.toFixed(2)}%</td>
                <td>{s.换手率?.toFixed(2)}%</td>
                <td>{fmtAmt(s.成交额)}</td>
                {subTab === "量比" && <td>{(s as any).量比?.toFixed(2) || "-"}</td>}
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
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
      <div className="table-wrap"><table className="stock-table">
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
      </table></div>
    </div>
  );
}

function OrdersTab({ onTrade: _onTrade }: { onTrade: () => void }) {
  const [orders, setOrders] = useState<PendingOrder[]>([]);
  const [statusFilter, setStatusFilter] = useState("pending");

  const load = useCallback(async () => {
    const data = await api.getOrders(statusFilter || undefined);
    setOrders(data);
    // 顺便触发委托单检查
    if (statusFilter === "pending") {
      await api.checkOrders();
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  const handleCancel = async (id: number) => {
    const res = await api.cancelOrder(id) as any;
    if (res.success) { toast("已撤单"); load(); }
    else alert(res.error);
  };

  const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleString();

  return (
    <div>
      <div className="filters">
        <label>状态<select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="pending">待成交</option>
          <option value="filled">已成交</option>
          <option value="cancelled">已撤单</option>
          <option value="">全部</option>
        </select></label>
        <button onClick={load}>刷新</button>
      </div>
      {orders.length === 0 ? <div className="empty">暂无委托单</div> : (
        <div className="table-wrap"><table className="stock-table">
          <thead>
            <tr><th>时间</th><th>操作</th><th>代码</th><th>名称</th><th>数量</th><th>委托价</th><th>状态</th><th>成交价</th><th>操作</th></tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id}>
                <td>{fmtTime(o.created_at)}</td>
                <td className={o.action === "buy" ? "buy-label" : "sell-label"}>{o.action === "buy" ? "买入" : "卖出"}</td>
                <td>{o.code}</td>
                <td>{o.name}</td>
                <td>{o.quantity}</td>
                <td>¥{o.target_price.toFixed(3)}</td>
                <td>{o.status === "pending" ? "待成交" : o.status === "filled" ? "已成交" : "已撤单"}</td>
                <td>{o.filled_price ? `¥${o.filled_price.toFixed(3)}` : "-"}</td>
                <td>{o.status === "pending" && <button className="cancel-btn" onClick={() => handleCancel(o.id)}>撤单</button>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}

function AnalysisTab() {
  const [snapshots, setSnapshots] = useState<DailySnapshot[]>([]);
  const [stats, setStats] = useState<PerformanceStats | null>(null);
  const [indices, setIndices] = useState<{ code: string; name: string; current: number; yesterday: number; change_pct: number }[]>([]);
  const [range, setRange] = useState(90);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartApiRef = useRef<IChartApi | null>(null);

  const loadData = useCallback(async () => {
    const [sn, st, idx] = await Promise.all([api.getDailySnapshots(range), api.getPerformance(), api.getIndices()]);
    setSnapshots(sn.reverse());
    setStats(st);
    setIndices(idx);
  }, [range]);

  useEffect(() => { loadData(); }, [loadData]);

  // Render equity curve chart
  useEffect(() => {
    const container = chartRef.current;
    if (!container || !snapshots.length) return;
    if (chartApiRef.current) {
      chartApiRef.current.remove();
      chartApiRef.current = null;
    }

    const raf = requestAnimationFrame(() => {
      if (!container) return;
      const chart = createChart(container, {
        width: container.clientWidth || 800,
        height: 280,
        layout: { background: { type: ColorType.Solid, color: "#161b22" }, textColor: "#c9d1d9" },
        grid: { vertLines: { color: "#21262d" }, horzLines: { color: "#21262d" } },
        timeScale: { borderColor: "#30363d" },
      });
      chartApiRef.current = chart;

      const totalLine = chart.addSeries(LineSeries, {
        color: "#58a6ff",
        lineWidth: 2,
        title: "总资产",
      });
      totalLine.setData(snapshots.map((s) => ({ time: s.date, value: s.total })));

      // 基准线（初始资金10万）
      const baseline = chart.addSeries(LineSeries, {
        color: "#484f58",
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "初始资金",
      });
      baseline.setData(snapshots.map((s) => ({ time: s.date, value: 100000 })));

      chart.timeScale().fitContent();
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
      if (chartApiRef.current) { chartApiRef.current.remove(); chartApiRef.current = null; }
    };
  }, [snapshots]);

  if (!stats) return <div className="loading">加载中...</div>;

  return (
    <div>
      <div className="filters">
        <label>时间范围<select value={range} onChange={(e) => setRange(Number(e.target.value))}>
          <option value={7}>1周</option>
          <option value={30}>1月</option>
          <option value={90}>3月</option>
          <option value={365}>全部</option>
        </select></label>
        <button onClick={async () => { await api.recordSnapshot(); loadData(); }}>记录快照</button>
      </div>

      {snapshots.length === 0 ? (
        <div className="empty">暂无资产数据，点击"记录快照"开始</div>
      ) : (
        <>
          <div className="analysis-chart-section">
            <h3>资金曲线</h3>
            <div ref={chartRef} className="chart-container" />
          </div>

          <div className="stats-grid">
            <div className="stat-card">
              <div className="stat-label">总收益率</div>
              <div className={`stat-value ${stats.total_return >= 0 ? "profit" : "loss"}`}>
                {stats.total_return >= 0 ? "+" : ""}{stats.total_return}%
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-label">年化收益率</div>
              <div className={`stat-value ${stats.annualized_return >= 0 ? "profit" : "loss"}`}>
                {stats.annualized_return >= 0 ? "+" : ""}{stats.annualized_return}%
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-label">最大回撤</div>
              <div className="stat-value loss">-{stats.max_drawdown}%</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">交易胜率</div>
              <div className="stat-value">{stats.win_rate}%</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">盈亏比</div>
              <div className="stat-value">{stats.profit_loss_ratio.toFixed(2)}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">平均持仓天数</div>
              <div className="stat-value">{stats.avg_holding_days}</div>
            </div>
          </div>

          {/* 大盘对比 */}
          {indices.length > 0 && stats && (
            <div className="index-compare">
              <h3>大盘对比</h3>
              <div className="stats-grid">
                {indices.map((idx) => {
                  const outperform = stats.total_return - idx.change_pct;
                  return (
                    <div key={idx.code} className="stat-card">
                      <div className="stat-label">{idx.name}</div>
                      <div className={`stat-value ${idx.change_pct >= 0 ? "profit" : "loss"}`}>
                        {idx.change_pct >= 0 ? "+" : ""}{idx.change_pct}%
                      </div>
                      <div className={`stat-detail ${outperform >= 0 ? "profit" : "loss"}`}>
                        {outperform >= 0 ? "跑赢" : "跑输"} {Math.abs(outperform).toFixed(2)}%
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
