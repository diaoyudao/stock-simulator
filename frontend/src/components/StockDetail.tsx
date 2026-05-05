import { useState, useEffect, useRef, useMemo } from "react";
import { api, type StockDetail as StockDetailType, type KLineItem, type Position, type FinancialAbstract, type FinancialStatement, type StockNews, type IntradayItem, type BidAskData, type FundFlowItem, type AIAnalysis, type AIScore } from "../api";
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, type IChartApi, type ISeriesApi, ColorType } from "lightweight-charts";
import { calcMA, calcBOLL, calcMACD, calcKDJ, calcRSI, type CandleData } from "../utils/indicators";
import { fmtAmt, fmtCap, fmt } from "../utils/format";
import { useTradingTime, toast } from "../utils/shared";

// 分时图组件
function IntradayChart({ data, basePrice }: { data: IntradayItem[]; basePrice: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const svg = useMemo(() => {
    if (!data.length) return "";
    const byMinute = new Map<string, { price: number; vol: number }>();
    for (const item of data) {
      const key = item.time.substring(0, 5);
      const prev = byMinute.get(key);
      if (!prev) byMinute.set(key, { price: item.price, vol: item.volume });
      else { prev.price = item.price; prev.vol += item.volume; }
    }
    const entries = [...byMinute.entries()];
    if (!entries.length) return "";
    const prices = entries.map(([, v]) => v.price);
    const minP = Math.min(...prices);
    const maxP = Math.max(...prices);
    const range = maxP - minP || 1;
    const w = 400;
    const h = 200;
    const pad = { l: 50, r: 10, t: 10, b: 30 };
    const cw = w - pad.l - pad.r;
    const ch = h - pad.t - pad.b;
    let svg = `<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}">`;
    svg += `<rect width="${w}" height="${h}" fill="#161b22"/>`;
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (ch / 4) * i;
      const p = maxP - (range / 4) * i;
      svg += `<line x1="${pad.l}" y1="${y}" x2="${w - pad.r}" y2="${y}" stroke="#30363d" stroke-width="0.5"/>`;
      svg += `<text x="${pad.l - 4}" y="${y + 3}" text-anchor="end" fill="#8b949e" font-size="10">${p.toFixed(2)}</text>`;
    }
    const bp = basePrice || prices[0];
    const baseY = pad.t + ch * (1 - (bp - minP) / range);
    if (baseY > pad.t && baseY < pad.t + ch) {
      svg += `<line x1="${pad.l}" y1="${baseY}" x2="${w - pad.r}" y2="${baseY}" stroke="#8b949e" stroke-dasharray="3,3" stroke-width="0.5"/>`;
    }
    const pts = entries.map(([_t, v], i) => {
      const x = pad.l + (cw / Math.max(entries.length - 1, 1)) * i;
      const y = pad.t + ch * (1 - (v.price - minP) / range);
      return `${x},${y}`;
    }).join(" ");
    svg += `<polyline points="${pts}" fill="none" stroke="#58a6ff" stroke-width="1.5"/>`;
    const step = Math.max(1, Math.floor(entries.length / 5));
    for (let i = 0; i < entries.length; i += step) {
      const x = pad.l + (cw / Math.max(entries.length - 1, 1)) * i;
      svg += `<text x="${x}" y="${h - 5}" text-anchor="middle" fill="#8b949e" font-size="10">${entries[i][0]}</text>`;
    }
    svg += `</svg>`;
    return svg;
  }, [data, basePrice]);

  useEffect(() => {
    if (ref.current && svg) ref.current.innerHTML = svg;
  }, [svg]);

  return <div className="intraday-chart-wrap" ref={ref} />;
}

export default function StockDetail({ code, positions, onBack, onTrade, onAddCompare }: {
  code: string;
  positions: Position[];
  onBack: () => void;
  onTrade: () => void;
  onAddCompare: (code: string, name: string) => void;
}) {
  const { isTradingTime, tradingStatus } = useTradingTime();
  const [detail, setDetail] = useState<StockDetailType | null>(null);
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
  const [detailTab, setDetailTab] = useState<"kline" | "financial" | "news" | "intraday" | "bidask" | "fundflow" | "ai">("kline");
  const [finType, setFinType] = useState<"abstract" | "利润表" | "资产负债表" | "现金流量表">("abstract");
  const [financialData, setFinancialData] = useState<FinancialAbstract[] | FinancialStatement[]>([]);
  const [financialError, setFinancialError] = useState("");
  const [financialLoading, setFinancialLoading] = useState(false);
  const [newsData, setNewsData] = useState<StockNews[]>([]);
  const [newsError, setNewsError] = useState("");
  const [intradayData, setIntradayData] = useState<IntradayItem[]>([]);
  const [bidAskData, setBidAskData] = useState<BidAskData | null>(null);
  const [fundFlowData, setFundFlowData] = useState<FundFlowItem[]>([]);
  const [aiAnalysis, setAiAnalysis] = useState<AIAnalysis | null>(null);
  const [aiScore, setAiScore] = useState<AIScore | null>(null);
  const [aiLoading, setAiLoading] = useState(false);

  const pos = positions.find((p) => p.code === code);

  useEffect(() => {
    Promise.all([api.getDetail(code), api.getHistory(code, klinePeriod)])
      .then(([d, k]) => { setDetail(d); setKlineData(k); });
  }, [code, klinePeriod]);

  const candleData = useMemo<CandleData[]>(
    () => klineData.map((d) => ({
      time: d.day, open: parseFloat(d.open), high: parseFloat(d.high),
      low: parseFloat(d.low), close: parseFloat(d.close), volume: parseFloat(d.volume),
    })),
    [klineData]
  );

  const indicatorData = useMemo(() => {
    if (!candleData.length) return null;
    return {
      ma5: calcMA(candleData, 5), ma10: calcMA(candleData, 10),
      ma20: calcMA(candleData, 20), ma60: calcMA(candleData, 60),
      boll: calcBOLL(candleData),
      macd: calcMACD(candleData), kdj: calcKDJ(candleData), rsi: calcRSI(candleData),
    };
  }, [candleData]);

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
    if (detailTab !== "ai" || !code) return;
    setAiLoading(true);
    Promise.all([api.getAIScore(code), api.getAIAnalysis(code)])
      .then(([score, analysis]) => {
        setAiScore(score && !score.error ? score : null);
        setAiAnalysis(analysis && !analysis.error ? analysis : null);
      })
      .catch(() => { setAiScore(null); setAiAnalysis(null); })
      .finally(() => setAiLoading(false));
  }, [code, detailTab]);

  // 图表生命周期
  useEffect(() => {
    const container = chartRef.current;
    if (!container || !klineData.length) return;
    if (chartApiRef.current) {
      chartApiRef.current.remove();
      chartApiRef.current = null;
    }

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

      const cd: CandleData[] = klineData.map((d) => ({
        time: d.day,
        open: parseFloat(d.open),
        high: parseFloat(d.high),
        low: parseFloat(d.low),
        close: parseFloat(d.close),
        volume: parseFloat(d.volume),
      }));
      candleSeries.setData(cd.map((d) => ({ time: d.time, open: d.open, high: d.high, low: d.low, close: d.close })));
      volumeSeries.setData(cd.map((d) => ({
        time: d.time,
        value: d.volume,
        color: d.close >= d.open ? "rgba(248,81,73,0.3)" : "rgba(63,185,80,0.3)",
      })));

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
      indicatorSeriesRef.current = [];
    };
  }, [klineData]);

  const indicatorSeriesRef = useRef<ISeriesApi<"Line" | "Histogram">[]>([]);

  // 指标系列
  useEffect(() => {
    const chart = chartApiRef.current;
    if (!chart || !indicatorData) return;

    for (const series of indicatorSeriesRef.current) {
      chart.removeSeries(series);
    }
    indicatorSeriesRef.current = [];

    const hasSubChart = indicators.subChart !== "none";
    chart.applyOptions({ height: hasSubChart ? 420 : 320 });
    const tracked = indicatorSeriesRef.current;

    if (indicators.ma) {
      const maColors: Record<number, string> = { 5: "#f0b429", 10: "#2ecc71", 20: "#e74c3c", 60: "#9b59b6" };
      for (const period of [5, 10, 20, 60] as const) {
        const maData = indicatorData[`ma${period}` as keyof typeof indicatorData] as import("../utils/indicators").LinePoint[];
        if (maData?.length) {
          const line = chart.addSeries(LineSeries, {
            color: maColors[period],
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
            title: `MA${period}`,
          });
          line.setData(maData);
          tracked.push(line as any);
        }
      }
    }

    if (indicators.boll && indicatorData.boll.length) {
      const upper = chart.addSeries(LineSeries, {
        color: "#e74c3c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "BOLL上轨",
      });
      const mid = chart.addSeries(LineSeries, {
        color: "#f0b429", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, title: "BOLL中轨",
      });
      const lower = chart.addSeries(LineSeries, {
        color: "#2ecc71", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "BOLL下轨",
      });
      upper.setData(indicatorData.boll.map((d) => ({ time: d.time, value: d.upper })));
      mid.setData(indicatorData.boll.map((d) => ({ time: d.time, value: d.mid })));
      lower.setData(indicatorData.boll.map((d) => ({ time: d.time, value: d.lower })));
      tracked.push(upper as any, mid as any, lower as any);
    }

    if (indicators.subChart === "macd" && indicatorData.macd.length) {
      const difLine = chart.addSeries(LineSeries, {
        color: "#f0b429", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "DIF",
      }, 1);
      const deaLine = chart.addSeries(LineSeries, {
        color: "#58a6ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "DEA",
      }, 1);
      const macdHist = chart.addSeries(HistogramSeries, {
        priceLineVisible: false, lastValueVisible: false,
      }, 1);
      difLine.setData(indicatorData.macd.map((d) => ({ time: d.time, value: d.dif })));
      deaLine.setData(indicatorData.macd.map((d) => ({ time: d.time, value: d.dea })));
      macdHist.setData(indicatorData.macd.map((d) => ({
        time: d.time, value: d.histogram,
        color: d.histogram >= 0 ? "rgba(248,81,73,0.6)" : "rgba(63,185,80,0.6)",
      })));
      tracked.push(difLine as any, deaLine as any, macdHist as any);
    } else if (indicators.subChart === "kdj" && indicatorData.kdj.length) {
      const kLine = chart.addSeries(LineSeries, {
        color: "#f0b429", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "K",
      }, 1);
      const dLine = chart.addSeries(LineSeries, {
        color: "#58a6ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "D",
      }, 1);
      const jLine = chart.addSeries(LineSeries, {
        color: "#e74c3c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "J",
      }, 1);
      kLine.setData(indicatorData.kdj.map((d) => ({ time: d.time, value: d.k })));
      dLine.setData(indicatorData.kdj.map((d) => ({ time: d.time, value: d.d })));
      jLine.setData(indicatorData.kdj.map((d) => ({ time: d.time, value: d.j })));
      tracked.push(kLine as any, dLine as any, jLine as any);
    } else if (indicators.subChart === "rsi" && indicatorData.rsi.length) {
      const rsi6 = chart.addSeries(LineSeries, {
        color: "#f0b429", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "RSI6",
      }, 1);
      const rsi12 = chart.addSeries(LineSeries, {
        color: "#58a6ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "RSI12",
      }, 1);
      const rsi24 = chart.addSeries(LineSeries, {
        color: "#e74c3c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "RSI24",
      }, 1);
      rsi6.setData(indicatorData.rsi.map((d) => ({ time: d.time, value: d.rsi6 })));
      rsi12.setData(indicatorData.rsi.map((d) => ({ time: d.time, value: d.rsi12 })));
      rsi24.setData(indicatorData.rsi.map((d) => ({ time: d.time, value: d.rsi24 })));
      tracked.push(rsi6 as any, rsi12 as any, rsi24 as any);
    }
  }, [indicators, indicatorData]);

  if (!detail) return <div className="loading">加载中...</div>;

  const price = detail["最新价"];
  const changeClass = detail["涨跌幅"] >= 0 ? "profit" : "loss";

  const executeTrade = async () => {
    if (tradeMode === "limit") {
      const res = await api.createOrder(code, detail["名称"], tradeAction, tradeQty, limitPrice) as any;
      if (res.error) { alert(res.error); return; }
    } else if (tradeAction === "buy") {
      const res = await api.buy(code, detail["名称"], tradeQty) as any;
      if (res.error) { alert(res.error); return; }
    } else {
      const res = await api.sell(code, tradeQty) as any;
      if (res.error) { alert(res.error); return; }
    }
    toast(tradeMode === "limit" ? "委托已提交" : `${tradeAction === "buy" ? "买入" : "卖出"}成功`);
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
        <button className="watch-btn" onClick={async () => { await api.addWatchlist(code, detail ? detail["名称"] : ""); toast("已加自选"); }}>加自选</button>
        <button className="watch-btn" onClick={() => { onAddCompare(code, detail ? detail["名称"] : ""); toast("已加对比"); }}>加对比</button>
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
            const res = await api.createAlert(code, detail["名称"], alertCondition, alertValue) as any;
            if (res.success) { setShowAlert(false); toast("提醒已设置"); }
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
            {(["kline", "intraday", "bidask", "fundflow", "financial", "news", "ai"] as const).map((t) => (
              <button key={t} className={detailTab === t ? "tab active" : "tab"} onClick={() => setDetailTab(t)}>
                {t === "kline" ? "K线" : t === "intraday" ? "分时" : t === "bidask" ? "盘口" : t === "fundflow" ? "资金" : t === "financial" ? "财务" : t === "ai" ? "AI分析" : "资讯"}
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
              <IntradayChart data={intradayData} basePrice={detail?.["昨收"] || 0} />
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
                  {fundFlowData.slice(0, 20).map((r, i) => (
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
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {detailTab === "ai" && (
          <div className="ai-content">
            {aiLoading ? (
              <div className="ai-loading">
                <div className="ai-spinner" />
                <span>AI分析中，请稍候...</span>
              </div>
            ) : (
              <>
                {aiScore && (
                  <div className="ai-scores">
                    <div className="ai-score-item">
                      <span className="ai-score-label">技术面</span>
                      <div className="ai-score-bar"><div className="ai-score-fill" style={{ width: `${aiScore.scores.technical}%`, background: aiScore.scores.technical >= 60 ? "#3fb950" : aiScore.scores.technical >= 40 ? "#d29922" : "#f85149" }} /></div>
                      <span className="ai-score-value">{aiScore.scores.technical}</span>
                    </div>
                    <div className="ai-score-item">
                      <span className="ai-score-label">基本面</span>
                      <div className="ai-score-bar"><div className="ai-score-fill" style={{ width: `${aiScore.scores.fundamental}%`, background: aiScore.scores.fundamental >= 60 ? "#3fb950" : aiScore.scores.fundamental >= 40 ? "#d29922" : "#f85149" }} /></div>
                      <span className="ai-score-value">{aiScore.scores.fundamental}</span>
                    </div>
                    <div className="ai-score-item">
                      <span className="ai-score-label">资金面</span>
                      <div className="ai-score-bar"><div className="ai-score-fill" style={{ width: `${aiScore.scores.capital}%`, background: aiScore.scores.capital >= 60 ? "#3fb950" : aiScore.scores.capital >= 40 ? "#d29922" : "#f85149" }} /></div>
                      <span className="ai-score-value">{aiScore.scores.capital}</span>
                    </div>
                    <div className="ai-score-item ai-score-overall">
                      <span className="ai-score-label">综合</span>
                      <span className="ai-score-total">{aiScore.scores.overall}</span>
                    </div>
                  </div>
                )}
                {aiAnalysis?.analysis && (
                  <div className="ai-report">
                    {aiAnalysis.analysis.technical && <div className="ai-section"><h4>技术面分析</h4><p>{aiAnalysis.analysis.technical}</p></div>}
                    {aiAnalysis.analysis.fundamental && <div className="ai-section"><h4>基本面评估</h4><p>{aiAnalysis.analysis.fundamental}</p></div>}
                    {aiAnalysis.analysis.capital && <div className="ai-section"><h4>资金面判断</h4><p>{aiAnalysis.analysis.capital}</p></div>}
                    {aiAnalysis.analysis.risk && <div className="ai-section"><h4>风险提示</h4><p>{aiAnalysis.analysis.risk}</p></div>}
                    {aiAnalysis.analysis.score > 0 && <div className="ai-section"><h4>综合评分</h4><p className="ai-rating">{aiAnalysis.analysis.score}/10</p></div>}
                  </div>
                )}
                {!aiScore && !aiAnalysis && !aiLoading && (
                  <div className="data-error"><span className="error-icon">!</span><span>AI分析暂不可用，请检查GLM_API_KEY配置</span></div>
                )}
                <div className="ai-disclaimer">{aiAnalysis?.disclaimer || aiScore?.disclaimer || "AI分析仅供参考，不构成投资建议"}</div>
              </>
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
