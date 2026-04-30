const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  return res.json();
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

export interface Dashboard {
  account: AccountInfo;
  positions: Position[];
  market_status: MarketStatus;
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
  buy: (code: string, name: string, quantity: number) =>
    request("/trade/buy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name, quantity }),
    }),
  sell: (code: string, quantity: number) =>
    request("/trade/sell", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, quantity }),
    }),
  reset: () => request("/trade/reset", { method: "POST" }),
  getWatchlist: () => request<WatchlistItem[]>("/trade/watchlist"),
  addWatchlist: (code: string, name: string) =>
    request("/trade/watchlist/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name }),
    }),
  removeWatchlist: (code: string) =>
    request("/trade/watchlist/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    }),
  getMarketStatus: () => request<MarketStatus>("/trade/market-status"),
  getDashboard: () => request<Dashboard>("/trade/dashboard"),
  createOrder: (code: string, name: string, action: "buy" | "sell", quantity: number, target_price: number) =>
    request("/trade/order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name, action, quantity, target_price }),
    }),
  getOrders: (status?: string) => {
    const qs = status ? `?status=${status}` : "";
    return request<PendingOrder[]>(`/trade/orders${qs}`);
  },
  cancelOrder: (id: number) =>
    request(`/trade/order/${id}/cancel`, { method: "POST" }),
  checkOrders: () =>
    request<{ filled_count: number; filled_orders: any[] }>("/trade/orders/check", { method: "POST" }),
};
