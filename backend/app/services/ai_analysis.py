"""AI股票分析服务 — 智谱GLM API集成 + 规则评分引擎"""

import json
import os
import time
import logging
import asyncio
import httpx
from pathlib import Path
from app.services.market_data import (
    get_stock_by_code, get_stock_history, get_fund_flow,
    get_financial_abstract, get_stock_news, BoundedCache,
    _safe_float, compute_consecutive_days, _all_caches,
    get_etf_by_code, get_etf_history, get_etf_fund_flow,
)

logger = logging.getLogger(__name__)


def _load_env():
    """惰性加载 .env 文件（兜底，正常由 run.py 加载）。"""
    if os.environ.get("GLM_API_KEY"):
        return
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

GLM_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def _get_glm_config():
    _load_env()
    return {
        "model": os.environ.get("GLM_MODEL", "glm-4-flash"),
        "api_key": os.environ.get("GLM_API_KEY", ""),
    }

CACHE_TIMEOUT = 300  # 5分钟缓存
_analysis_cache: BoundedCache = BoundedCache(128)
_score_cache: BoundedCache = BoundedCache(256)
_all_caches.append((_analysis_cache, CACHE_TIMEOUT))
_all_caches.append((_score_cache, CACHE_TIMEOUT))

DISCLAIMER = "AI分析仅供参考，不构成投资建议，投资有风险，入市需谨慎。"


def _get_stock_or_etf(code: str) -> tuple[dict, bool]:
    """获取股票或ETF数据。返回 (data, is_etf)。"""
    s = get_stock_by_code(code)
    if s:
        return s, False
    s = get_etf_by_code(code)
    if s:
        return s, True
    return {}, False


def _build_analysis_prompt(code: str) -> str:
    """构建LLM分析的prompt，聚合多维度数据。支持A股和ETF。"""
    stock, is_etf = _get_stock_or_etf(code)
    if not stock:
        return ""

    price = stock.get("最新价", 0)
    change_pct = stock.get("涨跌幅", 0)
    name = stock.get("名称", code)
    amount = stock.get("成交额", 0)

    if is_etf:
        from app.services.market_data import _compute_etf_consecutive
        consec = _compute_etf_consecutive(code)
        fund_flow = get_etf_fund_flow(code)
        fund_summary = ""
        for f in fund_flow[:5]:
            fund_summary += f"  {f.get('date', '')}: 主力净流入{_safe_float(f.get('main_net')):.0f}, 超大单{_safe_float(f.get('huge_net')):.0f}\n"
        etf_type = stock.get("_type_name", "其他")
        prompt = f"""你是一位专业的基金分析师。请根据以下数据对ETF {name}({code})进行综合分析:

【基本行情】最新价: {price}, 涨跌幅: {change_pct}%, 成交额: {amount/1e4:.0f}万
【基金类型】{etf_type}
【连涨连跌】连涨{consec['连涨天数']}天, 连跌{consec['连跌天数']}天
【资金流向（近5日）】
{fund_summary if fund_summary else "  暂无数据"}

请从以下维度给出简洁专业的分析（每项2-3句话）:
1. 技术面分析（趋势/支撑压力/指标信号）
2. 基本面评估（基金类型/跟踪指数/配置价值）
3. 资金面判断（主力动向）
4. 风险提示
5. 综合评分（1-10分，10分最看好）

请用JSON格式回复:
{{"technical": "技术面分析...", "fundamental": "基本面评估...", "capital": "资金面判断...", "risk": "风险提示...", "score": 7}}"""
        return prompt

    # A股逻辑
    turnover = stock.get("换手率", 0)
    pe = stock.get("市盈率-动态", 0)
    pb = stock.get("市净率", 0)
    mktcap = stock.get("总市值", 0)
    consec = compute_consecutive_days(code)
    fund_flow = get_fund_flow(code)
    fund_summary = ""
    for f in fund_flow[:5]:
        fund_summary += f"  {f.get('date', '')}: 主力净流入{_safe_float(f.get('main_net')):.0f}万, 超大单{_safe_float(f.get('huge_net')):.0f}万\n"
    financial = get_financial_abstract(code)
    fin_summary = ""
    for f in financial[:2]:
        fin_summary += f"  {f.get('报告期', f.get('日期', ''))}: "
        for k, v in list(f.items())[:6]:
            if k not in ("报告期", "日期"):
                fin_summary += f"{k}={v} "
        fin_summary += "\n"
    news = get_stock_news(code)
    news_summary = ""
    for n in news[:5]:
        news_summary += f"  - {n.get('title', '')} ({n.get('source', '')} {n.get('time', '')})\n"

    prompt = f"""你是一位专业的A股分析师。请根据以下数据对{name}({code})进行综合分析:

【基本行情】最新价: {price}, 涨跌幅: {change_pct}%, 换手率: {turnover}%, 市盈率(动): {pe}, 市净率: {pb}, 总市值: {mktcap/1e8:.1f}亿, 成交额: {amount/1e4:.0f}万
【连涨连跌】连涨{consec['连涨天数']}天, 连跌{consec['连跌天数']}天
【资金流向（近5日）】
{fund_summary if fund_summary else "  暂无数据"}
【财务概况（最近2期）】
{fin_summary if fin_summary else "  暂无数据"}
【近期资讯】
{news_summary if news_summary else "  暂无资讯"}

请从以下维度给出简洁专业的分析（每项2-3句话）:
1. 技术面分析（趋势/支撑压力/指标信号）
2. 基本面评估（估值/成长性）
3. 资金面判断（主力动向）
4. 风险提示
5. 综合评分（1-10分，10分最看好）

请用JSON格式回复:
{{"technical": "技术面分析...", "fundamental": "基本面评估...", "capital": "资金面判断...", "risk": "风险提示...", "score": 7}}"""

    return prompt


async def get_ai_analysis(code: str) -> dict:
    """调用GLM API获取AI综合分析。"""
    # 缓存检查
    now = time.time()
    if code in _analysis_cache:
        ts, data = _analysis_cache[code]
        if now - ts < CACHE_TIMEOUT:
            return data

    cfg = _get_glm_config()
    if not cfg["api_key"]:
        return {"error": "未配置GLM_API_KEY", "disclaimer": DISCLAIMER}

    prompt = _build_analysis_prompt(code)
    if not prompt:
        return {"error": "数据不存在", "disclaimer": DISCLAIMER}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GLM_API_URL,
                headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
                json={
                    "model": cfg["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 1000,
                },
            )
            resp.raise_for_status()
            result = resp.json()

        content = result["choices"][0]["message"]["content"]

        # 尝试解析JSON
        try:
            # 提取JSON部分（可能被markdown代码块包裹）
            json_str = content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0]
            analysis = json.loads(json_str.strip())
        except (json.JSONDecodeError, IndexError):
            # JSON解析失败，返回原始文本
            analysis = {
                "technical": content,
                "fundamental": "",
                "capital": "",
                "risk": "",
                "score": 0,
            }

        result_data = {
            "code": code,
            "analysis": analysis,
            "disclaimer": DISCLAIMER,
        }
        _analysis_cache[code] = (now, result_data)
        return result_data

    except httpx.HTTPStatusError as e:
        logger.warning("GLM API HTTP错误: %s", e)
        return {"error": f"AI服务请求失败({e.response.status_code})", "disclaimer": DISCLAIMER}
    except Exception as e:
        logger.warning("GLM API调用失败: %s", e)
        return {"error": f"AI服务暂时不可用: {e}", "disclaimer": DISCLAIMER}


async def get_ai_score(code: str) -> dict:
    """基于规则的技术面/基本面/资金面评分，即时返回。"""
    now = time.time()
    if code in _score_cache:
        ts, data = _score_cache[code]
        if now - ts < CACHE_TIMEOUT:
            return data

    # 同步计算在线程池执行，避免阻塞事件循环
    result = await asyncio.to_thread(_compute_score, code)
    if "error" not in result:
        _score_cache[code] = (now, result)
    return result


def _compute_score(code: str) -> dict:
    """同步评分计算逻辑。支持A股和ETF。"""
    stock, is_etf = _get_stock_or_etf(code)
    if not stock:
        return {"error": "股票/ETF不存在"}

    # ─── 技术面评分 ───
    tech_score = 50  # 基准分
    klines = get_etf_history(code, "daily") if is_etf else get_stock_history(code, "daily")
    if len(klines) >= 20:
        closes = [float(k["close"]) for k in klines[-20:]]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        price = closes[-1]

        # MA多头排列加分
        if ma5 > ma10 > ma20:
            tech_score += 15
        elif ma5 > ma10:
            tech_score += 8

        # 价格在MA上方加分
        if price > ma5:
            tech_score += 5
        if price > ma20:
            tech_score += 5

        # MACD信号（简化：5日均价 vs 10日均价趋势）
        if ma5 > ma10 and len(closes) >= 11:
            prev_ma5 = sum(closes[-6:-1]) / 5
            prev_ma10 = sum(closes[-11:-1]) / 10
            if prev_ma5 <= prev_ma10:
                tech_score += 10  # 金叉

        # RSI超买超卖（简化）
        if len(closes) >= 15:
            gains = []
            for i in range(1, min(15, len(closes))):
                diff = closes[-i] - closes[-i-1]
                gains.append(max(0, diff))
            avg_gain = sum(gains) / 14
            losses = [max(0, closes[-i-1] - closes[-i]) for i in range(1, min(15, len(closes)))]
            avg_loss = sum(losses) / 14
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                rsi = 100 - 100 / (1 + rs)
                if rsi < 30:
                    tech_score += 10  # 超卖
                elif rsi > 70:
                    tech_score -= 10  # 超买

    # ─── 基本面评分 ───
    fund_score = 50
    if is_etf:
        # ETF基本面：基金类型、跟踪指数表现
        fund_type = stock.get("_type_name", "其他")
        if fund_type in ("指数", "跨境"):
            fund_score += 10
        elif fund_type == "债券":
            fund_score += 15
        elif fund_type == "商品":
            fund_score += 5
    else:
        pe = stock.get("市盈率-动态", 0)
        pb = stock.get("市净率", 0)
        mktcap = stock.get("总市值", 0)
        if 0 < pe < 20:
            fund_score += 15
        elif 20 <= pe < 50:
            fund_score += 5
        elif pe < 0:
            fund_score -= 10
        if 0 < pb < 1:
            fund_score += 10
        elif 1 <= pb < 3:
            fund_score += 5
        if 0 < mktcap < 50e8:
            fund_score += 5

    # ─── 资金面评分 + 详情 ───
    capital_score = 50
    capital_detail = None
    fund_flow = get_etf_fund_flow(code) if is_etf else get_fund_flow(code)
    if fund_flow:
        recent = fund_flow[:3] if len(fund_flow) >= 3 else fund_flow
        main_net = sum(_safe_float(f.get("main_net", 0)) for f in recent)
        if main_net > 0:
            capital_score += min(20, int(main_net / 1000))
        else:
            capital_score += max(-20, int(main_net / 1000))

        huge_net = sum(_safe_float(f.get("huge_net", 0)) for f in recent)
        if huge_net > 0:
            capital_score += 5
        elif huge_net < 0:
            capital_score -= 5

        # 最近一天的资金面详情
        d = fund_flow[0]
        d_main_net = _safe_float(d.get("main_net", 0))
        d_main_pct = _safe_float(d.get("main_pct", 0))
        d_huge_net = _safe_float(d.get("huge_net", 0))
        d_huge_pct = _safe_float(d.get("huge_pct", 0))
        d_big_net = _safe_float(d.get("big_net", 0))
        d_big_pct = _safe_float(d.get("big_pct", 0))
        d_mid_net = _safe_float(d.get("mid_net", 0))
        d_mid_pct = _safe_float(d.get("mid_pct", 0))
        d_small_net = _safe_float(d.get("small_net", 0))
        d_small_pct = _safe_float(d.get("small_pct", 0))

        capital_detail = {
            "date": d.get("date", ""),
            "main_net": d_main_net,
            "main_pct": d_main_pct,
            "huge_net": d_huge_net,
            "huge_pct": d_huge_pct,
            "big_net": d_big_net,
            "big_pct": d_big_pct,
            "mid_net": d_mid_net,
            "mid_pct": d_mid_pct,
            "small_net": d_small_net,
            "small_pct": d_small_pct,
            "retail_net": d_mid_net + d_small_net,
            "retail_pct": d_mid_pct + d_small_pct,
            "main_buy_ratio": min(100, max(0, round(50 + d_main_pct / 2, 1))),
            "retail_buy_ratio": min(100, max(0, round(50 + (d_mid_pct + d_small_pct) / 2, 1))),
        }

    # ─── 综合评分 ───
    overall = round(tech_score * 0.4 + fund_score * 0.3 + capital_score * 0.3)
    overall = max(0, min(100, overall))

    return {
        "code": code,
        "name": stock.get("名称", code),
        "scores": {
            "technical": max(0, min(100, tech_score)),
            "fundamental": max(0, min(100, fund_score)),
            "capital": max(0, min(100, capital_score)),
            "overall": overall,
        },
        "capital_detail": capital_detail,
        "disclaimer": DISCLAIMER,
    }
