import { useState, useEffect, useRef, useCallback, createContext, useContext, useReducer, type ReactNode } from "react";
import { api, type MarketStatus } from "../api";

// ─── Toast — Context + useReducer 模式 ───

type ToastState = { msg: string; id: number };
type ToastAction = { type: "show"; msg: string } | { type: "hide" };

function toastReducer(state: ToastState, action: ToastAction): ToastState {
  switch (action.type) {
    case "show": return { msg: action.msg, id: state.id + 1 };
    case "hide": return state.id > 0 ? { msg: "", id: state.id } : state;
  }
}

const ToastContext = createContext<{
  state: ToastState;
  dispatch: React.Dispatch<ToastAction>;
}>({ state: { msg: "", id: 0 }, dispatch: () => {} });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(toastReducer, { msg: "", id: 0 });
  return <ToastContext.Provider value={{ state, dispatch }}>{children}</ToastContext.Provider>;
}

export function useToast() {
  const { dispatch } = useContext(ToastContext);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  return useCallback((msg: string) => {
    dispatch({ type: "show", msg });
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => dispatch({ type: "hide" }), 1500);
  }, [dispatch]);
}

// 保持 toast() 函数式调用兼容性（需在 ToastProvider 内使用）
let _globalToast: ((msg: string) => void) | null = null;
export function toast(msg: string) {
  if (_globalToast) { _globalToast(msg); return; }
  console.warn("toast() called outside ToastProvider");
}

export function Toast() {
  const { state } = useContext(ToastContext);
  const toastFn = useToast();

  useEffect(() => { _globalToast = toastFn; return () => { _globalToast = null; }; }, [toastFn]);

  if (!state.msg) return null;
  return <div className="toast" key={state.id}>{state.msg}</div>;
}

// ─── 感知页面可见性的轮询 hook ───

export function usePolling(callback: () => void, intervalMs: number, deps: readonly unknown[] = []) {
  const savedCb = useRef(callback);
  savedCb.current = callback;
  useEffect(() => {
    const tick = () => {
      if (document.visibilityState === "visible") savedCb.current();
    };
    tick();
    const id = setInterval(tick, intervalMs);
    const onVisible = () => { tick(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, deps);
}

// ─── 交易时间 ───

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

export const TradingTimeContext = createContext<{
  isTradingTime: boolean;
  tradingStatus: string;
  sessions: MarketStatus["sessions"];
  loading: boolean;
}>({
  isTradingTime: false, tradingStatus: "加载中", sessions: [], loading: true,
});

export function TradingTimeProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState({ isTradingTime: false, tradingStatus: "加载中", sessions: [] as MarketStatus["sessions"], loading: true });
  const fetchStatus = useCallback(() => {
    api.getMarketStatus().then((d) => {
      setInfo({ isTradingTime: d.is_trading_time, tradingStatus: d.status, sessions: d.sessions, loading: false });
    }).catch(() => {
      const fallback = checkTradingTimeLocal();
      setInfo({ isTradingTime: fallback.isTradingTime, tradingStatus: fallback.tradingStatus, sessions: [
        { name: "上午盘", start: "09:30", end: "11:30" },
        { name: "下午盘", start: "13:00", end: "15:00" },
      ], loading: false });
    });
  }, []);
  usePolling(fetchStatus, 30000);
  return <TradingTimeContext.Provider value={info}>{children}</TradingTimeContext.Provider>;
}

export function useTradingTime() {
  return useContext(TradingTimeContext);
}
