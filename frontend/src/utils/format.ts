/** 格式化金额（支持负数），亿/万 */
export function fmtAmt(v: number): string {
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(0) + "万";
  return v.toFixed(0);
}

/** 格式化市值（万亿/亿/万） */
export function fmtCap(v: number): string {
  if (!v) return "-";
  if (v >= 1e12) return (v / 1e12).toFixed(2) + "万亿";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(2) + "万";
  return v.toFixed(0);
}

/** 格式化价格，为空返回 "-" */
export function fmt(v: number, decimals = 2): string {
  return v ? v.toFixed(decimals) : "-";
}
