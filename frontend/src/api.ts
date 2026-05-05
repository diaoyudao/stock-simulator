const BASE = import.meta.env.VITE_API_URL || "/api";

// 简易 GET 请求缓存，避免重复请求同一只读接口
const _cache = new Map<string, { ts: number; data: unknown }>();
const _inflight = new Map<string, Promise<unknown>>();
const CACHE_TTL = 30_000; // 30秒
const MAX_CACHE_SIZE = 50;

function _cacheKey(path: string, init?: RequestInit): string | null {
  // 仅缓存 GET 请求
  if (init?.method && init.method !== "GET") return null;
  return path;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const key = _cacheKey(path, init);
  if (key) {
    const hit = _cache.get(key);
    if (hit && Date.now() - hit.ts < CACHE_TTL) return hit.data as T;
    // 请求去重：复用已在飞的 promise
    const inflight = _inflight.get(key);
    if (inflight) return inflight as Promise<T>;
  }
  const fetchPromise = (async () => {
    const res = await fetch(`${BASE}${path}`, init);
    const data = await res.json();
    if (!res.ok) {
      throw { status: res.status, detail: data.detail || data.error || `请求失败(${res.status})` };
    }
    if (key) {
      // 缓存上限：淘汰最旧条目
      if (_cache.size >= MAX_CACHE_SIZE) {
        const oldest = _cache.keys().next().value;
        if (oldest) _cache.delete(oldest);
      }
      _cache.set(key, { ts: Date.now(), data });
    }
    return data as T;
  })();
  if (key) {
    _inflight.set(key, fetchPromise);
    fetchPromise.finally(() => _inflight.delete(key));
  }
  return fetchPromise;
}

// 写操作后清除相关缓存
function invalidateCache(prefix?: string) {
  if (prefix) {
    for (const k of _cache.keys()) {
      if (k.startsWith(prefix)) _cache.delete(k);
    }
  } else {
    _cache.clear();
  }
}

export interface SpotResult {
  total: number;
  page: number;
  page_size: number;
  items: StockItem[];
}

export interface StockItem {
  代码: string;
  名称: string;
  最新价: number;
  涨跌幅: number;
  涨跌额: number;
  今开: number;
  最高: number;
  最低: number;
  昨收: number;
  买一: number;
  卖一: number;
  成交量: number;
  成交额: number;
  换手率: number;
  "市盈率-动态": number;
  市净率: number;
  总市值: number;
  流通市值: number;
  量比?: number;
  "52周最高"?: number;
  "52周最低"?: number;
}

export interface StockDetail extends StockItem {
  量比: number;
  "52周最高": number;
  "52周最低": number;
  连涨天数: number;
  连跌天数: number;
  行业: string;
}

export interface SectorItem {
  name: string;
  code: string;
}

export interface SectorOverviewItem {
  name: string;
  avg_change_pct: number;
  up_count: number;
  down_count: number;
  amount: number;
  new_high_count: number;
  new_low_count: number;
  top_stocks: { 代码: string; 名称: string; 涨跌幅: number }[];
}

export interface KLineItem {
  day: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}

export interface AccountInfo {
  cash: number;
  market_value: number;
  total_assets: number;
  total_profit: number;
  profit_pct: number;
}

export interface Position {
  code: string;
  name: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  profit: number;
  profit_pct: number;
}

export interface Transaction {
  id: number;
  code: string;
  name: string;
  action: "buy" | "sell";
  quantity: number;
  price: number;
  amount: number;
  created_at: number;
}

export interface WatchlistItem {
  code: string;
  name: string;
  price: number;
  change_pct: number;
  change_amt: number;
  group_id: number;
}

export interface WatchlistGroup {
  id: number;
  name: string;
  sort_order: number;
}

export interface MarketStatus {
  is_trading_time: boolean;
  status: string;
  sessions: { name: string; start: string; end: string }[];
}

export interface PendingOrder {
  id: number;
  code: string;
  name: string;
  action: "buy" | "sell";
  quantity: number;
  target_price: number;
  status: "pending" | "filled" | "cancelled";
  created_at: number;
  filled_at: number | null;
  filled_price: number | null;
}

export interface DailySnapshot {
  date: string;
  cash: number;
  positions_value: number;
  total: number;
}

export interface PerformanceStats {
  total_return: number;
  annualized_return: number;
  max_drawdown: number;
  win_rate: number;
  profit_loss_ratio: number;
  avg_holding_days: number;
  snapshot_count: number;
}

export interface Dashboard {
  account: AccountInfo;
  positions: Position[];
  market_status: MarketStatus;
}

export interface FinancialAbstract {
  [key: string]: string;
}

export interface FinancialStatement {
  [key: string]: string;
}

export interface StockNews {
  title: string;
  url: string;
  source: string;
  time: string;
}

export interface IntradayItem {
  time: string;
  price: number;
  volume: number;
  nature: string;
}

export interface BidAskData {
  buy_1: number; buy_1_vol: number;
  buy_2: number; buy_2_vol: number;
  buy_3: number; buy_3_vol: number;
  buy_4: number; buy_4_vol: number;
  buy_5: number; buy_5_vol: number;
  sell_1: number; sell_1_vol: number;
  sell_2: number; sell_2_vol: number;
  sell_3: number; sell_3_vol: number;
  sell_4: number; sell_4_vol: number;
  sell_5: number; sell_5_vol: number;
  latest: number;
  avg: number;
  limit_up: number;
  limit_down: number;
}

export interface FundFlowItem {
  date: string;
  close: number;
  change_pct: number;
  main_net: number;
  main_pct: number;
  huge_net: number;
  huge_pct: number;
  big_net: number;
  big_pct: number;
  mid_net: number;
  mid_pct: number;
  small_net: number;
  small_pct: number;
}

export interface LhbItem {
  代码: string;
  名称: string;
  上榜日: string;
  收盘价: number;
  涨跌幅: number;
  净买额: number;
  买入额: number;
  卖出额: number;
  成交额: number;
  换手率: number;
  上榜原因: string;
}

export interface AIAnalysis {
  code: string;
  analysis: {
    technical: string;
    fundamental: string;
    capital: string;
    risk: string;
    score: number;
  };
  disclaimer: string;
  error?: string;
}

export interface AIScore {
  code: string;
  name: string;
  scores: {
    technical: number;
    fundamental: number;
    capital: number;
    overall: number;
  };
  capital_detail?: {
    date: string;
    main_net: number;
    main_pct: number;
    huge_net: number;
    huge_pct: number;
    big_net: number;
    big_pct: number;
    mid_net: number;
    mid_pct: number;
    small_net: number;
    small_pct: number;
    retail_net: number;
    retail_pct: number;
    main_buy_ratio: number;
    retail_buy_ratio: number;
  };
  disclaimer: string;
  error?: string;
}

export const api = {
  getSpot: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)])
    ).toString();
    return request<SpotResult>(`/market/spot?${qs}`);
  },
  getDetail: (code: string) => request<StockDetail>(`/market/detail/${code}`),
  getSectors: () => request<SectorItem[]>("/market/sectors"),
  getSectorOverview: () => request<SectorOverviewItem[]>("/market/sector-overview"),
  getConsecutive: (code: string) => request<{连涨天数: number; 连跌天数: number}>(`/market/consecutive/${code}`),
  getHistory: (code: string, period: string = "daily") =>
    request<KLineItem[]>(`/market/history/${code}?period=${period}`),
  getAccount: () => request<AccountInfo>("/trade/account"),
  getPositions: () => request<Position[]>("/trade/positions"),
  getTransactions: (params: { limit?: number; start_date?: string; end_date?: string; action?: string } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v).map(([k, v]) => [k, String(v)])
    ).toString();
    return request<Transaction[]>(`/trade/transactions?${qs}`);
  },
  buy: (code: string, name: string, quantity: number) => {
    invalidateCache("/trade/");
    return request("/trade/buy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name, quantity }),
    });
  },
  sell: (code: string, quantity: number) => {
    invalidateCache("/trade/");
    return request("/trade/sell", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, quantity }),
    });
  },
  reset: () => {
    invalidateCache();
    return request("/trade/reset", { method: "POST" });
  },
  getWatchlist: (group_id?: number) => {
    const qs = group_id ? `?group_id=${group_id}` : "";
    return request<WatchlistItem[]>(`/trade/watchlist${qs}`);
  },
  addWatchlist: (code: string, name: string, group_id = 1) => {
    invalidateCache("/trade/watchlist");
    return request("/trade/watchlist/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name, group_id }),
    });
  },
  removeWatchlist: (code: string) => {
    invalidateCache("/trade/watchlist");
    return request("/trade/watchlist/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
  },
  moveWatchlist: (code: string, group_id: number) => {
    invalidateCache("/trade/watchlist");
    return request("/trade/watchlist/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, group_id }),
    });
  },
  getGroups: () => request<WatchlistGroup[]>("/trade/groups"),
  createGroup: (name: string) => {
    invalidateCache("/trade/group");
    return request("/trade/groups/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  },
  deleteGroup: (group_id: number) => {
    invalidateCache("/trade/group");
    return request("/trade/groups/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id }),
    });
  },
  getMarketStatus: () => request<MarketStatus>("/trade/market-status"),
  getDashboard: () => request<Dashboard>("/trade/dashboard"),
  createOrder: (code: string, name: string, action: "buy" | "sell", quantity: number, target_price: number) => {
    invalidateCache("/trade/order");
    return request("/trade/order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name, action, quantity, target_price }),
    });
  },
  getOrders: (status?: string) => {
    const qs = status ? `?status=${status}` : "";
    return request<PendingOrder[]>(`/trade/orders${qs}`);
  },
  cancelOrder: (id: number) => {
    invalidateCache("/trade/order");
    return request(`/trade/order/${id}/cancel`, { method: "POST" });
  },
  checkOrders: () => {
    invalidateCache("/trade/order");
    return request<{ filled_count: number; filled_orders: any[] }>("/trade/orders/check", { method: "POST" });
  },
  getDailySnapshots: (days = 90) =>
    request<DailySnapshot[]>(`/trade/daily-snapshots?days=${days}`),
  getPerformance: () =>
    request<PerformanceStats>("/trade/performance"),
  recordSnapshot: () => {
    invalidateCache("/trade/daily");
    return request("/trade/snapshot", { method: "POST" });
  },
  getIndices: () =>
    request<{ code: string; name: string; current: number; yesterday: number; change_pct: number }[]>("/market/indices"),
  createAlert: (code: string, name: string, condition: string, value: number) => {
    invalidateCache("/trade/alert");
    return request("/trade/alert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name, condition, value }),
    });
  },
  getAlerts: (status?: string) => {
    const qs = status ? `?status=${status}` : "";
    return request<{ id: number; code: string; name: string; condition: string; value: number; status: string; created_at: number; triggered_at: number | null; message: string | null }[]>(`/trade/alerts${qs}`);
  },
  cancelAlert: (id: number) => {
    invalidateCache("/trade/alert");
    return request(`/trade/alert/${id}/cancel`, { method: "POST" });
  },
  getFinancialAbstract: (code: string) =>
    request<FinancialAbstract[]>(`/market/financial/abstract/${code}`),
  getFinancialStatement: (code: string, type: string) =>
    request<FinancialStatement[]>(`/market/financial/statement/${code}?type=${encodeURIComponent(type)}`),
  getStockNews: (code: string) =>
    request<StockNews[]>(`/market/news/${code}`),
  getIntraday: (code: string) =>
    request<IntradayItem[]>(`/market/intraday/${code}`),
  getBidAsk: (code: string) =>
    request<BidAskData>(`/market/bidask/${code}`),
  getFundFlow: (code: string) =>
    request<FundFlowItem[]>(`/market/fund-flow/${code}`),
  getMinuteHistory: (code: string, period: string = "1") =>
    request<KLineItem[]>(`/market/minute/${code}?period=${period}`),
  getRanking: (sortBy: string = "涨跌幅", order: string = "desc", limit: number = 50) =>
    request<StockItem[]>(`/market/ranking?sort_by=${encodeURIComponent(sortBy)}&order=${order}&limit=${limit}`),
  getLhb: (days: number = 5) =>
    request<LhbItem[]>(`/market/lhb?days=${days}`),
  getAIAnalysis: (code: string) =>
    request<AIAnalysis>(`/ai/analyze/${code}`),
  getAIScore: (code: string) =>
    request<AIScore>(`/ai/score/${code}`),
};
