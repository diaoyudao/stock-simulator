/**
 * 技术指标计算模块 — 纯函数，无副作用
 * 输入：K线数据（OHLCV），输出：指标数据点（含 time 字段，可直接用于 lightweight-charts）
 */

export interface CandleData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface LinePoint {
  time: string;
  value: number;
}

export interface MACDPoint {
  time: string;
  dif: number;
  dea: number;
  histogram: number;
}

export interface KDJPoint {
  time: string;
  k: number;
  d: number;
  j: number;
}

export interface RSIMultiPoint {
  time: string;
  rsi6: number;
  rsi12: number;
  rsi24: number;
}

export interface BOLLPoint {
  time: string;
  upper: number;
  mid: number;
  lower: number;
}

// ─── 工具函数 ───

function ema(values: number[], period: number): (number | null)[] {
  const result: (number | null)[] = new Array(values.length).fill(null);
  if (values.length === 0) return result;
  const k = 2 / (period + 1);
  result[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    result[i] = values[i] * k + (result[i - 1] as number) * (1 - k);
  }
  return result;
}

// ─── MA 均线 ───

export function calcMA(candles: CandleData[], period: number): LinePoint[] {
  const result: LinePoint[] = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close;
    if (i >= period) sum -= candles[i - period].close;
    if (i >= period - 1) {
      result.push({ time: candles[i].time, value: sum / period });
    }
  }
  return result;
}

// ─── MACD ───

export function calcMACD(
  candles: CandleData[],
  fast = 12,
  slow = 26,
  signal = 9,
): MACDPoint[] {
  const closes = candles.map((c) => c.close);
  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);

  const difValues: (number | null)[] = emaFast.map((f, i) => {
    const s = emaSlow[i];
    return f !== null && s !== null ? f - s : null;
  });

  const validDif = difValues.filter((v): v is number => v !== null);
  const deaValues = ema(validDif, signal);

  const result: MACDPoint[] = [];
  for (let i = 0; i < candles.length; i++) {
    const d = difValues[i];
    const e = deaValues[i];
    if (d !== null && e !== null) {
      result.push({
        time: candles[i].time,
        dif: d,
        dea: e,
        histogram: (d - e) * 2,
      });
    }
  }
  return result;
}

// ─── KDJ ───

export function calcKDJ(
  candles: CandleData[],
  n = 9,
  m1 = 3,
  m2 = 3,
): KDJPoint[] {
  const result: KDJPoint[] = [];
  let prevK = 50;
  let prevD = 50;

  for (let i = 0; i < candles.length; i++) {
    const start = Math.max(0, i - n + 1);
    let lowN = Infinity;
    let highN = -Infinity;
    for (let j = start; j <= i; j++) {
      if (candles[j].low < lowN) lowN = candles[j].low;
      if (candles[j].high > highN) highN = candles[j].high;
    }

    const rsv = highN === lowN ? 50 : ((candles[i].close - lowN) / (highN - lowN)) * 100;
    const k = (2 / m1) * prevK + (1 / m1) * rsv;
    const d = (2 / m2) * prevD + (1 / m2) * k;
    const j = 3 * k - 2 * d;

    prevK = k;
    prevD = d;

    if (i >= n - 1) {
      result.push({ time: candles[i].time, k, d, j });
    }
  }
  return result;
}

// ─── RSI ───

export function calcRSI(
  candles: CandleData[],
  periods = [6, 12, 24],
): RSIMultiPoint[] {
  const closes = candles.map((c) => c.close);
  const maxPeriod = Math.max(...periods);
  if (closes.length < maxPeriod + 1) return [];

  // Pre-compute gains and losses
  const gains: number[] = [];
  const losses: number[] = [];
  for (let i = 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    gains.push(diff > 0 ? diff : 0);
    losses.push(diff < 0 ? -diff : 0);
  }

  // RSI for each period
  const rsiByPeriod: Map<number, (number | null)[]> = new Map();
  for (const period of periods) {
    const rsiValues: (number | null)[] = [];
    let avgGain = 0;
    let avgLoss = 0;

    for (let i = 0; i < gains.length; i++) {
      if (i < period - 1) {
        rsiValues.push(null);
        continue;
      }
      if (i === period - 1) {
        // Initial SMA
        for (let j = 0; j < period; j++) {
          avgGain += gains[j];
          avgLoss += losses[j];
        }
        avgGain /= period;
        avgLoss /= period;
      } else {
        avgGain = (avgGain * (period - 1) + gains[i]) / period;
        avgLoss = (avgLoss * (period - 1) + losses[i]) / period;
      }
      const rsi = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
      rsiValues.push(rsi);
    }
    rsiByPeriod.set(period, rsiValues);
  }

  // Combine into multi-period result
  const result: RSIMultiPoint[] = [];
  for (let i = 0; i < candles.length - 1; i++) {
    const r6 = rsiByPeriod.get(6)![i];
    const r12 = rsiByPeriod.get(12)![i];
    const r24 = rsiByPeriod.get(24)![i];
    if (r6 !== null && r12 !== null && r24 !== null) {
      result.push({
        time: candles[i + 1].time, // +1 because gains/losses are offset by 1
        rsi6: r6,
        rsi12: r12,
        rsi24: r24,
      });
    }
  }
  return result;
}

// ─── BOLL 布林带 ───

export function calcBOLL(candles: CandleData[], period = 20, mult = 2): BOLLPoint[] {
  const result: BOLLPoint[] = [];
  for (let i = period - 1; i < candles.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) {
      sum += candles[j].close;
    }
    const mid = sum / period;

    let variance = 0;
    for (let j = i - period + 1; j <= i; j++) {
      variance += (candles[j].close - mid) ** 2;
    }
    const std = Math.sqrt(variance / period);

    result.push({
      time: candles[i].time,
      upper: mid + mult * std,
      mid,
      lower: mid - mult * std,
    });
  }
  return result;
}
