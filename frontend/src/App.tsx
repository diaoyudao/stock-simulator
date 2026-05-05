import { useState, useEffect, useCallback, useRef, useMemo, createContext, useContext } from "react";
import { api, type StockItem, type StockDetail, type KLineItem, type AccountInfo, type Position, type Transaction, type SectorOverviewItem, type WatchlistItem, type MarketStatus, type FinancialAbstract, type FinancialStatement, type StockNews, type IntradayItem, type BidAskData, type FundFlowItem, type LhbItem } from "./api";
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, type IChartApi, type CandlestickData, type HistogramData, ColorType } from "lightweight-charts";
import { calcMA, calcBOLL, calcMACD, calcKDJ, calcRSI, type CandleData, type MACDPoint, type KDJPoint, type RSIMultiPoint } from "./utils/indicators";
import "./App.css";

type Tab = "market" | "watchlist" | "sectors" | "ranking" | "positions" | "orders" | "analysis" | "transactions";

// 全局共享交易时间，避免每个组件创建独立 interval
const TradingTimeContext = createContext<{ isTradingTime: boolean; tradingStatus: string; sessions: MarketStatus["sessions"] }>({
  isTradingTime: false, tradingStatus: "加载中", sessions: [],
});

function TradingTimeProvider({ children }: { children: React.ReactNode }) {
  const [info, setInfo] = useState({ isTradingTime: false, tradingStatus: "加载中", sessions: [] as MarketStatus["sessions"] });
  useEffect(() => {
    const fetch = () => {
      api.getMarketStatus().then((d) => {
        setInfo({ isTradingTime: d.is_trading_time, tradingStatus: d.status, sessions: d.sessions });
      }).catch(() => {
        const fallback = checkTradingTimeLocal();
        setInfo({ isTradingTime: fallback.isTradingTime, tradingStatus: fallback.tradingStatus, sessions: [
          { name: "上午盘", start: "09:30", end: "11:30" },
          { name: "下午盘", start: "13:00", end: "15:00" },
        ]});
      });
    };
    fetch();
    const id = setInterval(fetch, 30000);
    return () => clearInterval(id);
  }, []);
  return <TradingTimeContext.Provider value={info}>{children}</TradingTimeContext.Provider>;
}

function useTradingTime() {
  return useContext(TradingTimeContext);
}

function checkTradingTimeLocal() {
  const now = new Date();
  const day = now.getDay();
  const h = now.getHours(), m = now.getMinutes(), t = h * 60 + m;
  if (day === 0 || day === 6) return { isTradingTime: false, tradingStatus: "休市（周末）" };
  if (t < 9 * 60 + 30) return { isTradingTime: false, tradingStatus: "尚未开盘" };
  if (t <= 11 * 60 + 30) return { isTradingTime: true, tradingStatus: "交易中" };
  if (t < 13 * 60) return { isTradingTime: false, tradingStatus: "午间休市" };
  if (t <= 15 * 60) return { isTradingTime: true, tradingStatus: "交易中" };
  return { isTradingTime: false, tradingStatus: "已收盘" };
}

export default function App() {
  return (
    <TradingTimeProvider>
      <AppInner />
    </TradingTimeProvider>
  );
}

function ComparePanel({ list, setList, onSelectStock }: {
  list: { code: string; name: string }[];
  setList: React.Dispatch<React.SetStateAction<{ code: string; name: string }[]>>;
  onSelectStock: (code: string) => void;
}) {
  const [details, setDetails] = useState<StockDetail[]>([]);
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

  const metrics: { label: string; key: keyof StockDetail; fmt: (v: number) => string }[] = [
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

  if (selectedStock) {
    return (
      <StockDetail
        code={selectedStock}
        positions={positions}
        onBack={() => setSelectedStock(null)}
        onTrade={refresh}
        onAddCompare={(code, name) => {
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
        {(["market", "watchlist", "sectors", "ranking", "positions", "orders", "analysis", "transactions"] as Tab[]).map((t) => (
          <button key={t} className={`tab${tab === t ? " active" : ""}`} data-tab={t} onClick={() => setTab(t)}>
            <span className="tab-icon">{t === "market" ? "📊" : t === "watchlist" ? "⭐" : t === "sectors" ? "🏭" : t === "ranking" ? "🏆" : t === "positions" ? "💰" : t === "orders" ? "📋" : t === "analysis" ? "📈" : "📒"}</span>
            <span className="tab-label">{t === "market" ? "行情" : t === "watchlist" ? "自选" : t === "sectors" ? "板块" : t === "ranking" ? "排行" : t === "positions" ? "持仓" : t === "orders" ? "委托" : t === "analysis" ? "分析" : "记录"}</span>
          </button>
        ))}
        <button className="tab reset" onClick={async () => { await api.reset(); refresh(); }}>重置</button>
      </nav>
      <main className="main">
        {tab === "market" && <MarketTab onTrade={refresh} onSelectStock={setSelectedStock} />}
        {tab === "watchlist" && <WatchlistTab onSelectStock={setSelectedStock} onTrade={refresh} />}
        {tab === "sectors" && <SectorsTab onSelectStock={setSelectedStock} />}
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

  const load = useCallback(async () => {
    const alerts = await api.getAlerts("triggered");
    setTriggeredAlerts(alerts.map((a: any) => ({ id: a.id, code: a.code, name: a.name, message: a.message || "" })));
  }, []);

  useEffect(() => { load(); }, [load]);

  // Poll every 60s
  useEffect(() => {
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [load]);

  const hasNew = triggeredAlerts.length > 0;

  return (
    <div className="notification-bell" onClick={() => setShowPanel(!showPanel)}>
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
        <div className="table-wrap"><table className="stock-table">
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
                <td>
                  <button className="watch-btn" onClick={() => api.addWatchlist(s["代码"], s["名称"])}>自选</button>
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
  const { isTradingTime } = useTradingTime();
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
  useEffect(() => {
    fetchList();
    const timer = setInterval(fetchList, 30000);
    return () => clearInterval(timer);
  }, [fetchList]);

  const handleRemove = async (code: string) => {
    await api.removeWatchlist(code);
    fetchList();
  };

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;
    await api.createGroup(newGroupName.trim());
    setNewGroupName("");
    fetchGroups();
  };

  const handleDeleteGroup = async (id: number) => {
    await api.deleteGroup(id);
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
                    <select className="group-select" value={s.group_id} onChange={(e) => handleMove(s.code, Number(e.target.value))}>
                      {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                    </select>
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

function SectorsTab({ onSelectStock }: { onSelectStock: (code: string) => void }) {
  const [sectors, setSectors] = useState<SectorOverviewItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    api.getSectorOverview().then(setSectors).finally(() => setLoading(false));
  }, []);

  const fmtAmt = (v: number) => {
    if (v >= 1e8) return (v / 1e8).toFixed(1) + "亿";
    if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
    return v.toFixed(0);
  };

  if (loading) return <div className="loading">加载中...</div>;
  return (
    <div className="table-wrap"><table className="stock-table">
      <thead>
        <tr><th>板块</th><th>均涨幅</th><th>涨/跌</th><th>成交额</th><th>新高</th><th>新低</th><th>领涨股</th></tr>
      </thead>
      <tbody>
        {sectors.map((s) => (
          <tr key={s.name}>
            <td className="sector-name">{s.name}</td>
            <td className={s.avg_change_pct >= 0 ? "profit" : "loss"}>{s.avg_change_pct >= 0 ? "+" : ""}{s.avg_change_pct.toFixed(2)}%</td>
            <td><span className="profit">{s.up_count}</span>/<span className="loss">{s.down_count}</span></td>
            <td>{fmtAmt(s.amount)}</td>
            <td className={s.new_high_count > 0 ? "profit" : ""}>{s.new_high_count || "-"}</td>
            <td className={s.new_low_count > 0 ? "loss" : ""}>{s.new_low_count || "-"}</td>
            <td>{s.top_stocks.map((t, i) => (
              <span key={t.代码}>{i > 0 && "、"}<button className="stock-link" onClick={() => onSelectStock(t.代码)}>{t.名称}</button><span className={t.涨跌幅 >= 0 ? "profit" : "loss"}>{t.涨跌幅 >= 0 ? "+" : ""}{t.涨跌幅}%</span></span>
            ))}</td>
          </tr>
        ))}
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

  const fmtAmt = (v: number) => {
    if (v >= 1e8) return (v / 1e8).toFixed(1) + "亿";
    if (v >= 1e4) return (v / 1e4).toFixed(0) + "万";
    return v.toFixed(0);
  };

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

function OrdersTab({ onTrade }: { onTrade: () => void }) {
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
    const res = await api.cancelOrder(id);
    if (res.success) load();
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

// ============ Stock Detail Page ============

function StockDetail({ code, positions, onBack, onTrade, onAddCompare }: {
  code: string;
  positions: Position[];
  onBack: () => void;
  onTrade: () => void;
  onAddCompare: (code: string, name: string) => void;
}) {
  const { isTradingTime, tradingStatus } = useTradingTime();
  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [klinePeriod, setKlinePeriod] = useState<"daily" | "weekly" | "monthly">("daily");
  const [klineData, setKlineData] = useState<KLineItem[]>([]);
  const [indicators, setIndicators] = useState({
    ma: true,
    boll: false,
    subChart: "none" as "none" | "macd" | "kdj" | "rsi",
  });
  const chartRef = useRef<HTMLDivElement>(null);
  const chartApiRef = useRef<IChartApi | null>(null);
  const [tradeAction, setTradeAction] = useState<"buy" | "sell">("buy");
  const [tradeQty, setTradeQty] = useState(100);
  const [tradeMode, setTradeMode] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState(0);
  const [showAlert, setShowAlert] = useState(false);
  const [alertCondition, setAlertCondition] = useState<"above" | "below">("above");
  const [alertValue, setAlertValue] = useState(0);
  const [detailTab, setDetailTab] = useState<"kline" | "financial" | "news" | "intraday" | "bidask" | "fundflow">("kline");
  const [finType, setFinType] = useState<"abstract" | "利润表" | "资产负债表" | "现金流量表">("abstract");
  const [financialData, setFinancialData] = useState<FinancialAbstract[] | FinancialStatement[]>([]);
  const [financialError, setFinancialError] = useState("");
  const [financialLoading, setFinancialLoading] = useState(false);
  const [newsData, setNewsData] = useState<StockNews[]>([]);
  const [newsError, setNewsError] = useState("");
  const [intradayData, setIntradayData] = useState<IntradayItem[]>([]);
  const [bidAskData, setBidAskData] = useState<BidAskData | null>(null);
  const [fundFlowData, setFundFlowData] = useState<FundFlowItem[]>([]);

  const pos = positions.find((p) => p.code === code);

  useEffect(() => {
    // 并行请求详情和K线数据
    Promise.all([api.getDetail(code), api.getHistory(code, klinePeriod)])
      .then(([d, k]) => { setDetail(d); setKlineData(k); });
  }, [code, klinePeriod]);

  useEffect(() => {
    if (detailTab !== "financial" || !code) return;
    setFinancialError("");
    setFinancialLoading(true);
    const req = finType === "abstract" ? api.getFinancialAbstract(code) : api.getFinancialStatement(code, finType);
    req.then((d) => {
      if (Array.isArray(d) && d.length > 0) {
        setFinancialData(d);
        setFinancialError("");
      } else if (Array.isArray(d) && d.length === 0) {
        setFinancialData([]);
        setFinancialError("该股票暂无财务数据");
      } else {
        setFinancialData([]);
        const msg = (d as any).detail || (d as any).error || "数据加载失败";
        setFinancialError(msg);
      }
    }).catch(() => {
      setFinancialData([]);
      setFinancialError("请求失败，请稍后重试");
    }).finally(() => setFinancialLoading(false));
  }, [code, detailTab, finType]);

  useEffect(() => {
    if (detailTab !== "news" || !code) return;
    setNewsError("");
    api.getStockNews(code).then((d) => {
      if (Array.isArray(d)) { setNewsData(d); setNewsError(""); }
      else { setNewsData([]); setNewsError((d as any).detail || "资讯加载失败"); }
    }).catch(() => { setNewsData([]); setNewsError("请求失败"); });
  }, [code, detailTab]);

  useEffect(() => {
    if (detailTab !== "intraday" || !code) return;
    api.getIntraday(code).then((d) => setIntradayData(Array.isArray(d) ? d : [])).catch(() => setIntradayData([]));
  }, [code, detailTab]);

  useEffect(() => {
    if (detailTab !== "bidask" || !code) return;
    api.getBidAsk(code).then((d) => setBidAskData(d && typeof d === "object" && !Array.isArray(d) ? d : null)).catch(() => setBidAskData(null));
  }, [code, detailTab]);

  useEffect(() => {
    if (detailTab !== "fundflow" || !code) return;
    api.getFundFlow(code).then((d) => setFundFlowData(Array.isArray(d) ? d : [])).catch(() => setFundFlowData([]));
  }, [code, detailTab]);

  useEffect(() => {
    const container = chartRef.current;
    if (!container || !klineData.length) return;
    if (chartApiRef.current) {
      chartApiRef.current.remove();
      chartApiRef.current = null;
    }

    const hasSubChart = indicators.subChart !== "none";
    const mainHeight = hasSubChart ? 240 : 320;
    const totalHeight = hasSubChart ? 420 : 320;

    const raf = requestAnimationFrame(() => {
      if (!container) return;
      const chart = createChart(container, {
        width: container.clientWidth || 800,
        height: totalHeight,
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

      const candleData: CandlestickData[] = klineData.map((d) => ({
        time: d.day,
        open: parseFloat(d.open),
        high: parseFloat(d.high),
        low: parseFloat(d.low),
        close: parseFloat(d.close),
      }));
      const volumeData: HistogramData[] = klineData.map((d) => ({
        time: d.day,
        value: parseFloat(d.volume),
        color: parseFloat(d.close) >= parseFloat(d.open) ? "rgba(248,81,73,0.3)" : "rgba(63,185,80,0.3)",
      }));
      candleSeries.setData(candleData);
      volumeSeries.setData(volumeData);

      // Convert K-line data to CandleData for indicator calculation
      const cd: CandleData[] = klineData.map((d) => ({
        time: d.day,
        open: parseFloat(d.open),
        high: parseFloat(d.high),
        low: parseFloat(d.low),
        close: parseFloat(d.close),
        volume: parseFloat(d.volume),
      }));

      // MA 均线
      if (indicators.ma) {
        const maColors: Record<number, string> = { 5: "#f0b429", 10: "#2ecc71", 20: "#e74c3c", 60: "#9b59b6" };
        for (const period of [5, 10, 20, 60] as const) {
          const maData = calcMA(cd, period);
          if (maData.length) {
            const line = chart.addSeries(LineSeries, {
              color: maColors[period],
              lineWidth: 1,
              priceLineVisible: false,
              lastValueVisible: false,
              title: `MA${period}`,
            });
            line.setData(maData);
          }
        }
      }

      // BOLL 布林带
      if (indicators.boll) {
        const bollData = calcBOLL(cd);
        if (bollData.length) {
          const upper = chart.addSeries(LineSeries, {
            color: "#e74c3c",
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
            title: "BOLL上轨",
          });
          const mid = chart.addSeries(LineSeries, {
            color: "#f0b429",
            lineWidth: 1,
            lineStyle: 2,
            priceLineVisible: false,
            lastValueVisible: false,
            title: "BOLL中轨",
          });
          const lower = chart.addSeries(LineSeries, {
            color: "#2ecc71",
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
            title: "BOLL下轨",
          });
          upper.setData(bollData.map((d) => ({ time: d.time, value: d.upper })));
          mid.setData(bollData.map((d) => ({ time: d.time, value: d.mid })));
          lower.setData(bollData.map((d) => ({ time: d.time, value: d.lower })));
        }
      }

      // 副图指标
      if (indicators.subChart === "macd") {
        const macdData = calcMACD(cd);
        if (macdData.length) {
          const difLine = chart.addSeries(LineSeries, {
            color: "#f0b429",
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
            title: "DIF",
          }, 1);
          const deaLine = chart.addSeries(LineSeries, {
            color: "#58a6ff",
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
            title: "DEA",
          }, 1);
          const macdHist = chart.addSeries(HistogramSeries, {
            priceLineVisible: false,
            lastValueVisible: false,
          }, 1);
          difLine.setData(macdData.map((d) => ({ time: d.time, value: d.dif })));
          deaLine.setData(macdData.map((d) => ({ time: d.time, value: d.dea })));
          macdHist.setData(macdData.map((d) => ({
            time: d.time,
            value: d.histogram,
            color: d.histogram >= 0 ? "rgba(248,81,73,0.6)" : "rgba(63,185,80,0.6)",
          })));
        }
      } else if (indicators.subChart === "kdj") {
        const kdjData = calcKDJ(cd);
        if (kdjData.length) {
          const kLine = chart.addSeries(LineSeries, {
            color: "#f0b429", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "K",
          }, 1);
          const dLine = chart.addSeries(LineSeries, {
            color: "#58a6ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "D",
          }, 1);
          const jLine = chart.addSeries(LineSeries, {
            color: "#e74c3c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "J",
          }, 1);
          kLine.setData(kdjData.map((d) => ({ time: d.time, value: d.k })));
          dLine.setData(kdjData.map((d) => ({ time: d.time, value: d.d })));
          jLine.setData(kdjData.map((d) => ({ time: d.time, value: d.j })));
        }
      } else if (indicators.subChart === "rsi") {
        const rsiData = calcRSI(cd);
        if (rsiData.length) {
          const rsi6 = chart.addSeries(LineSeries, {
            color: "#f0b429", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "RSI6",
          }, 1);
          const rsi12 = chart.addSeries(LineSeries, {
            color: "#58a6ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "RSI12",
          }, 1);
          const rsi24 = chart.addSeries(LineSeries, {
            color: "#e74c3c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "RSI24",
          }, 1);
          rsi6.setData(rsiData.map((d) => ({ time: d.time, value: d.rsi6 })));
          rsi12.setData(rsiData.map((d) => ({ time: d.time, value: d.rsi12 })));
          rsi24.setData(rsiData.map((d) => ({ time: d.time, value: d.rsi24 })));
        }
      }

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
      if (chartApiRef.current) {
        chartApiRef.current.remove();
        chartApiRef.current = null;
      }
    };
  }, [klineData, indicators]);

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
    if (tradeMode === "limit") {
      const res = await api.createOrder(code, detail["名称"], tradeAction, tradeQty, limitPrice);
      if (res.error) { alert(res.error); return; }
    } else if (tradeAction === "buy") {
      const res = await api.buy(code, detail["名称"], tradeQty);
      if (res.error) { alert(res.error); return; }
    } else {
      const res = await api.sell(code, tradeQty);
      if (res.error) { alert(res.error); return; }
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
        <button className="watch-btn" onClick={() => api.addWatchlist(code, detail ? detail["名称"] : "")}>加自选</button>
        <button className="watch-btn" onClick={() => onAddCompare(code, detail ? detail["名称"] : "")}>加对比</button>
        <button className="watch-btn" onClick={() => { setAlertValue(price); setShowAlert(!showAlert); }}>提醒</button>
        <div className="detail-title">
          <span className="detail-name">{detail["名称"]}</span>
          <span className="detail-code">{detail["代码"]}</span>
        </div>
      </div>
      {showAlert && (
        <div className="alert-panel">
          <select value={alertCondition} onChange={(e) => setAlertCondition(e.target.value as "above" | "below")}>
            <option value="above">涨到</option>
            <option value="below">跌到</option>
          </select>
          <input type="number" value={alertValue} step={0.01} onChange={(e) => setAlertValue(Number(e.target.value))} />
          <button onClick={async () => {
            const res = await api.createAlert(code, detail["名称"], alertCondition, alertValue);
            if (res.success) { setShowAlert(false); alert("提醒已设置"); }
            else alert(res.error);
          }}>确认</button>
          <button onClick={() => setShowAlert(false)}>取消</button>
        </div>
      )}
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

      {/* Detail sub-tabs */}
      <div className="detail-chart-section">
        <div className="chart-toolbar">
          <div className="period-tabs detail-main-tabs">
            {(["kline", "intraday", "bidask", "fundflow", "financial", "news"] as const).map((t) => (
              <button key={t} className={detailTab === t ? "tab active" : "tab"} onClick={() => setDetailTab(t)}>
                {t === "kline" ? "K线" : t === "intraday" ? "分时" : t === "bidask" ? "盘口" : t === "fundflow" ? "资金" : t === "financial" ? "财务" : "资讯"}
              </button>
            ))}
          </div>
          {detailTab === "kline" && (
            <div className="indicator-tabs">
              {(["daily", "weekly", "monthly"] as const).map((p) => (
                <button key={p} className={klinePeriod === p ? "tab active" : "tab"} onClick={() => setKlinePeriod(p)}>
                  {p === "daily" ? "日K" : p === "weekly" ? "周K" : "月K"}
                </button>
              ))}
              <button className={indicators.ma ? "tab active" : "tab"} onClick={() => setIndicators((p) => ({ ...p, ma: !p.ma }))}>MA</button>
              <button className={indicators.boll ? "tab active" : "tab"} onClick={() => setIndicators((p) => ({ ...p, boll: !p.boll }))}>BOLL</button>
              <button className={indicators.subChart === "macd" ? "tab active" : "tab"} onClick={() => setIndicators((p) => ({ ...p, subChart: p.subChart === "macd" ? "none" : "macd" }))}>MACD</button>
              <button className={indicators.subChart === "kdj" ? "tab active" : "tab"} onClick={() => setIndicators((p) => ({ ...p, subChart: p.subChart === "kdj" ? "none" : "kdj" }))}>KDJ</button>
              <button className={indicators.subChart === "rsi" ? "tab active" : "tab"} onClick={() => setIndicators((p) => ({ ...p, subChart: p.subChart === "rsi" ? "none" : "rsi" }))}>RSI</button>
            </div>
          )}
          {detailTab === "financial" && (
            <div className="indicator-tabs">
              {(["abstract", "利润表", "资产负债表", "现金流量表"] as const).map((t) => (
                <button key={t} className={finType === t ? "tab active" : "tab"} onClick={() => setFinType(t)}>
                  {t === "abstract" ? "财务摘要" : t}
                </button>
              ))}
            </div>
          )}
        </div>

        {detailTab === "kline" && <div ref={chartRef} className="chart-container" />}

        {detailTab === "financial" && (
          <div className="financial-content">
            {financialError ? (
              <div className="data-error">
                <span className="error-icon">!</span>
                <span>{financialError}</span>
              </div>
            ) : financialLoading ? (
              <div className="loading">加载中...</div>
            ) : financialData.length === 0 ? null : (
              <div className="financial-table-wrap">
                <table className="financial-table">
                  <thead>
                    <tr>
                      <th>指标</th>
                      {financialData.map((row, i) => (
                        <th key={i}>{row["报告期"] || row["报告日"] || `第${i + 1}期`}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.keys(financialData[0])
                      .filter((k) => k !== "报告期" && k !== "报告日")
                      .map((key) => (
                        <tr key={key}>
                          <td className="fin-label">{key}</td>
                          {financialData.map((row, i) => (
                            <td key={i}>{row[key] ?? "-"}</td>
                          ))}
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {detailTab === "news" && (
          <div className="news-content">
            {newsError ? (
              <div className="data-error">
                <span className="error-icon">!</span>
                <span>{newsError}</span>
              </div>
            ) : newsData.length === 0 ? (
              <div className="empty-hint">暂无资讯</div>
            ) : (
              newsData.map((n, i) => (
                <a key={i} className="news-item" href={n.url} target="_blank" rel="noopener noreferrer">
                  <span className="news-title">{n.title}</span>
                  <span className="news-meta">{n.source} · {n.time}</span>
                </a>
              ))
            )}
          </div>
        )}

        {detailTab === "intraday" && (
          <div className="intraday-content">
            {intradayData.length === 0 ? (
              <div className="data-error"><span className="error-icon">!</span><span>暂无分时数据</span></div>
            ) : (
              <div className="intraday-chart-wrap" ref={(el) => {
                if (!el || !intradayData.length) return;
                // Aggregate by minute for price line
                const byMinute = new Map<string, { price: number; vol: number }>();
                for (const item of intradayData) {
                  const key = item.time.substring(0, 5);
                  const prev = byMinute.get(key);
                  if (!prev) byMinute.set(key, { price: item.price, vol: item.volume });
                  else { prev.price = item.price; prev.vol += item.volume; }
                }
                const entries = [...byMinute.entries()];
                if (!entries.length) return;
                const prices = entries.map(([, v]) => v.price);
                const minP = Math.min(...prices);
                const maxP = Math.max(...prices);
                const range = maxP - minP || 1;
                const w = el.clientWidth || 400;
                const h = 200;
                const pad = { l: 50, r: 10, t: 10, b: 30 };
                const cw = w - pad.l - pad.r;
                const ch = h - pad.t - pad.b;
                const basePrice = detail?.["昨收"] || prices[0];
                let svg = `<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">`;
                // Grid lines
                for (let i = 0; i <= 4; i++) {
                  const y = pad.t + (ch / 4) * i;
                  const p = maxP - (range / 4) * i;
                  svg += `<line x1="${pad.l}" y1="${y}" x2="${w-pad.r}" y2="${y}" stroke="#30363d" stroke-width="0.5"/>`;
                  svg += `<text x="${pad.l-4}" y="${y+3}" text-anchor="end" fill="#8b949e" font-size="10">${p.toFixed(2)}</text>`;
                }
                // Base price line
                const baseY = pad.t + ch * (1 - (basePrice - minP) / range);
                if (baseY > pad.t && baseY < pad.t + ch) {
                  svg += `<line x1="${pad.l}" y1="${baseY}" x2="${w-pad.r}" y2="${baseY}" stroke="#8b949e" stroke-dasharray="3,3" stroke-width="0.5"/>`;
                }
                // Price line
                const pts = entries.map(([t, v], i) => {
                  const x = pad.l + (cw / Math.max(entries.length - 1, 1)) * i;
                  const y = pad.t + ch * (1 - (v.price - minP) / range);
                  return `${x},${y}`;
                }).join(" ");
                svg += `<polyline points="${pts}" fill="none" stroke="#58a6ff" stroke-width="1.5"/>`;
                // Time labels
                const step = Math.max(1, Math.floor(entries.length / 5));
                for (let i = 0; i < entries.length; i += step) {
                  const x = pad.l + (cw / Math.max(entries.length - 1, 1)) * i;
                  svg += `<text x="${x}" y="${h-5}" text-anchor="middle" fill="#8b949e" font-size="10">${entries[i][0]}</text>`;
                }
                svg += `</svg>`;
                el.innerHTML = svg;
              }} />
            )}
          </div>
        )}

        {detailTab === "bidask" && (
          <div className="bidask-content">
            {!bidAskData ? (
              <div className="data-error"><span className="error-icon">!</span><span>盘口数据加载失败</span></div>
            ) : (
              <div className="bidask-grid">
                <div className="bidask-sell-side">
                  {[5, 4, 3, 2, 1].map((i) => (
                    <div key={i} className="bidask-row sell-row">
                      <span className="bidask-label">卖{ i}</span>
                      <span className="bidask-price loss">{bidAskData[`sell_${i}` as keyof BidAskData].toFixed(2)}</span>
                      <span className="bidask-vol">{(bidAskData[`sell_${i}_vol` as keyof BidAskData] as number / 100).toFixed(0)}手</span>
                    </div>
                  ))}
                </div>
                <div className="bidask-divider">
                  <span className="bidask-latest">{bidAskData.latest.toFixed(2)}</span>
                  <span className="bidask-limits">涨停 {bidAskData.limit_up.toFixed(2)} / 跌停 {bidAskData.limit_down.toFixed(2)}</span>
                </div>
                <div className="bidask-buy-side">
                  {[1, 2, 3, 4, 5].map((i) => (
                    <div key={i} className="bidask-row buy-row">
                      <span className="bidask-label">买{i}</span>
                      <span className="bidask-price profit">{bidAskData[`buy_${i}` as keyof BidAskData].toFixed(2)}</span>
                      <span className="bidask-vol">{(bidAskData[`buy_${i}_vol` as keyof BidAskData] as number / 100).toFixed(0)}手</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {detailTab === "fundflow" && (
          <div className="fundflow-content">
            {fundFlowData.length === 0 ? (
              <div className="data-error"><span className="error-icon">!</span><span>资金流向数据加载失败</span></div>
            ) : (
              <table className="stock-table fundflow-table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>收盘</th>
                    <th>涨跌%</th>
                    <th>主力净流入</th>
                    <th>主力占比</th>
                    <th>超大单</th>
                    <th>大单</th>
                    <th>中单</th>
                    <th>小单</th>
                  </tr>
                </thead>
                <tbody>
                  {fundFlowData.slice(0, 20).map((r, i) => {
                    const fmtAmt = (v: number) => {
                      if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + "亿";
                      if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(0) + "万";
                      return v.toFixed(0);
                    };
                    return (
                      <tr key={i}>
                        <td>{r.date}</td>
                        <td>{r.close.toFixed(2)}</td>
                        <td className={r.change_pct >= 0 ? "profit" : "loss"}>{r.change_pct >= 0 ? "+" : ""}{r.change_pct.toFixed(2)}%</td>
                        <td className={r.main_net >= 0 ? "profit" : "loss"}>{fmtAmt(r.main_net)}</td>
                        <td className={r.main_pct >= 0 ? "profit" : "loss"}>{r.main_pct >= 0 ? "+" : ""}{r.main_pct.toFixed(2)}%</td>
                        <td className={r.huge_net >= 0 ? "profit" : "loss"}>{fmtAmt(r.huge_net)}</td>
                        <td className={r.big_net >= 0 ? "profit" : "loss"}>{fmtAmt(r.big_net)}</td>
                        <td className={r.mid_net >= 0 ? "profit" : "loss"}>{fmtAmt(r.mid_net)}</td>
                        <td className={r.small_net >= 0 ? "profit" : "loss"}>{fmtAmt(r.small_net)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
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
          <select className="trade-mode" value={tradeMode} onChange={(e) => setTradeMode(e.target.value as "market" | "limit")}>
            <option value="market">市价</option>
            <option value="limit">限价</option>
          </select>
        </div>
        <div className="detail-trade-row">
          <span>当前价: ¥{fmt(price)}</span>
          {tradeMode === "limit" && (
            <label>委托价<input type="number" value={limitPrice} step={0.01} min={0.01} onChange={(e) => setLimitPrice(Number(e.target.value))} /></label>
          )}
          <label>数量(股)<input type="number" value={tradeQty} step={100} min={100} onChange={(e) => setTradeQty(Math.max(100, Math.round(Number(e.target.value) / 100) * 100))} /></label>
          <span>金额: ¥{(tradeQty * (tradeMode === "limit" ? limitPrice : price)).toFixed(2)}</span>
        </div>
        <button className="detail-confirm" onClick={executeTrade} disabled={!isTradingTime}>
          {tradeMode === "market" ? "确认" : "提交委托"}{tradeAction === "buy" ? "买入" : "卖出"}
        </button>
      </div>
    </div>
  );
}
