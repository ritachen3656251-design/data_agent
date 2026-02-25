# mapper.py
# LLM 映射：用户问题 -> intent + dt/days/metric_focus/not_supported（不输出 tool_key/calls）
# 支持 session_ctx 记忆补全：缺 dt/days/metric_focus 时沿用上一轮

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

# session_ctx 结构：{"last_dt": "YYYY-MM-DD"|None, "last_days": int|None, "last_metric_focus": str|None}
# 用于多轮对话时补全用户未指定的 dt/days/metric_focus

# 参考日，用于 昨天/前天 计算
REFERENCE_DATE = "2017-12-04"

# 使用 last_days 的意图（趋势/漏斗/留存/日活/诊断）
INTENTS_NEED_DAYS = {"overview_daily", "funnel_daily", "user_retention", "user_activity", "diagnose_generic"}

# 诊断默认 metric_focus
DEFAULT_METRIC_FOCUS = "uv_to_buyer"

INTENTS = [
    "overview_day",
    "overview_daily",
    "funnel_daily",
    "category_contrib_buyers",
    "user_retention",
    "user_activity",
    "new_vs_old_user_conversion",
    "diagnose_generic",
    "unknown",
]

MAPPER_PROMPT = """你是一个查询意图映射器。根据用户问题，输出严格 JSON，不得有其他文字。

## 输出 schema
{
  "intent": "overview_day|overview_daily|funnel_daily|category_contrib_buyers|user_retention|user_activity|new_vs_old_user_conversion|diagnose_generic|unknown",
  "dt": "YYYY-MM-DD 或 null",
  "days": 1-90 的整数或 null,
  "assumptions": ["字符串列表"],
  "not_supported": null 或 {"metric": "xxx", "reason": "缺什么", "missing_fields": "字段名"}
}

## 日期规则（必须识别）
- 中文日期：X月Y日（1-12月、1-31日均需识别），如 11月25日、11月30日、12月1日、12月3日 等
- 点号日期：12.03、12.3、12.1、11.25
- 斜杠日期：12/3、12/1
- 短横线日期：12-03、12-01
- 完整日期：2017-12-01
- 相对日期：昨天、前天、今天、当天、当日
- 范围表达：最近一周、最近N天、过去N天、前N天、两周、半个月、一个月
- dt 必须输出 YYYY-MM-DD。缺年份默认 2017，并写 assumptions：["日期无年份，默认 2017 年"]
- 假设当前参考日 {ref_date}，昨天={yesterday}，前天={day_before}，今天/当天={ref_date}
- 最近一周/这周/本周 -> days=7；两周/14天 -> days=14；半个月 -> days=15；一个月 -> days=30；最近N天 -> days=N（1-90）

## intent 选择规则（含模糊表达）
- overview_day：核心指标、指标如何、数据怎么样、数据情况、表现如何、看下数据、查下、帮我看看、UV多少、买家多少、PV、加购用户、单日数据 + 有 dt
- overview_daily：趋势、最近、过去、近N天、数据走势、整体情况、整体表现、无明确单日
- funnel_daily：漏斗、转化链、加购到购买、UV到购买、转化、转化率（无新老）、转化表现、漏斗表现
- category_contrib_buyers：类目、品类、分类、贡献、拖累、拉动、哪些类目、什么类目、哪个类目、类目Top
- user_retention：留存、次留、次日留存、7日留存、留存率
- user_activity：日活、DAU、活跃、活跃用户、活跃数
- new_vs_old_user_conversion：新老、新用户、老用户、新老转化、新老差异、新老对比
- diagnose_generic：为什么、原因、上升、下降、掉、跌、下滑、变差、掉了、波动、怎么回事、诊断
- not_supported：GMV、成交额、客单价、ROI、订单数、ARPU、销售额、交易额

## 模糊语言映射（优先按语义推断）
- "数据"、"怎么样"、"如何"、"看下"、"查下"、"帮我"、"看一下" -> 结合上下文推断 intent；无日期时默认 overview_daily(days=9)
- "那天"、"当日"、"当天" -> dt=参考日
- "这周"、"本周"、"上周" -> days=7

## 注意
- 不输出 tool_key、calls
- 只输出合法 JSON，无 markdown 包裹
"""


def _get_ref_dates() -> tuple[str, str, str]:
    """参考日、昨天、前天。"""
    d = datetime.strptime(REFERENCE_DATE, "%Y-%m-%d")
    return (
        REFERENCE_DATE,
        (d - timedelta(days=1)).strftime("%Y-%m-%d"),
        (d - timedelta(days=2)).strftime("%Y-%m-%d"),
    )


def _call_llm(question: str) -> str:
    """调用 LLM，返回原始文本。"""
    try:
        from dashscope import Generation

        ref, yesterday, day_before = _get_ref_dates()
        prompt = MAPPER_PROMPT.format(ref_date=ref, yesterday=yesterday, day_before=day_before)
        r = Generation.call(
            model="qwen-max",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"用户问题：{question}"},
            ],
            result_format="message",
        )
        if r.status_code == 200:
            text = (r.output.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            return text
    except Exception:
        pass
    return ""


def _parse_json(text: str) -> dict | None:
    """从 LLM 输出提取 JSON。"""
    text = text.strip()
    # 去掉 markdown 代码块
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试找 { ... }
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def _fallback_normalize(question: str) -> dict:
    """轻量正则提取 dt/days/prev_dt，支持多种模糊表达。日期范围（X月Y日到Z日）用于两日对比分析。"""
    dt, days, prev_dt, assumptions = None, None, None, []
    q = question.strip()

    # 日期范围：12月1日到2日、12月1日到12月2日、11月30日到12月2日
    m = re.search(r"(\d{1,2})月(\d{1,2})[号日]?\s*到\s*(\d{1,2})月(\d{1,2})[号日]?", q)
    if m:
        m1, d1, m2, d2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if 1 <= m1 <= 12 and 1 <= d1 <= 31 and 1 <= m2 <= 12 and 1 <= d2 <= 31:
            dt1 = f"2017-{m1:02d}-{d1:02d}"
            dt2 = f"2017-{m2:02d}-{d2:02d}"
            prev_dt, dt = (dt1, dt2) if dt1 <= dt2 else (dt2, dt1)
            assumptions.append("日期无年份，默认 2017 年")
    else:
        m = re.search(r"(\d{1,2})月(\d{1,2})[号日]?\s*到\s*(\d{1,2})[号日]?", q)
        if m:
            mo, d1, d2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d1 <= 31 and 1 <= d2 <= 31:
                dt1 = f"2017-{mo:02d}-{d1:02d}"
                dt2 = f"2017-{mo:02d}-{d2:02d}"
                prev_dt, dt = (dt1, dt2) if d1 <= d2 else (dt2, dt1)
                assumptions.append("日期无年份，默认 2017 年")

    # YYYY-MM-DD / 2017-12-01（无范围时）
    if dt is None:
        m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", q)
        if m:
            dt = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        else:
            # 点号 12.03/12.1、斜杠 12/3/12/1、短横线 12-03/12-01、中文 12月1日
            for pat in [r"(\d{1,2})[./\-](\d{1,2})(?![.\d])", r"(\d{1,2})月(\d{1,2})[号日]?"]:
                m = re.search(pat, q)
                if m:
                    mo, d = int(m.group(1)), int(m.group(2))
                    if 1 <= mo <= 12 and 1 <= d <= 31:
                        dt = f"2017-{mo:02d}-{d:02d}"
                        assumptions.append("日期无年份，默认 2017 年")
                    break

    # 模糊日期：昨天/前天/今天/当天/当日（参考日 2017-12-04）
    if dt is None:
        if "昨天" in q:
            dt = "2017-12-03"
        elif "前天" in q:
            dt = "2017-12-02"
        elif any(k in q for k in ["今天", "当天", "当日", "那天"]):
            dt = REFERENCE_DATE

    # 天数：最近N天 / 近N天 / 过去N天 / 前N天
    m = re.search(r"最近\s*(\d+)\s*天|近\s*(\d+)\s*天|过去\s*(\d+)\s*天|前\s*(\d+)\s*天", q)
    if m:
        days = min(int(m.group(1) or m.group(2) or m.group(3) or m.group(4)), 90)
    elif any(k in q for k in ["最近一周", "最近1周", "一周", "近一周", "这周", "本周"]):
        days = 7
    elif any(k in q for k in ["两周", "14天"]):
        days = 14
    elif any(k in q for k in ["半个月"]):
        days = 15
    elif any(k in q for k in ["一个月", "最近一月", "近一月"]):
        days = 30

    return {"dt": dt, "days": days, "prev_dt": prev_dt, "assumptions": assumptions}


def _fallback_map(question: str) -> dict:
    """LLM 失败时规则回退，支持多种模糊表达。"""
    n = _fallback_normalize(question)
    q = (question or "").lower().strip()
    intent = "unknown"
    dt = n.get("dt")
    days = n.get("days")
    prev_dt = n.get("prev_dt")
    assumptions = list(n.get("assumptions", []))
    not_supported = None

    # not_supported 优先
    if any(k in q for k in ["gmv", "成交额", "销售额", "交易额", "客单价", "roi", "arpu", "订单数"]):
        intent = "unknown"
        metric = "GMV" if "gmv" in q or "成交额" in q else ("客单价" if "客单价" in q or "arpu" in q else ("ROI" if "roi" in q else ("订单数" if "订单" in q else "该指标")))
        not_supported = {"metric": metric, "reason": "无价格/金额字段", "missing_fields": "price,amount"}
    # 暂时无法查询：新老转化率、单独买家数、日活、次日留存（产品策略：说明暂时无法查询）
    elif any(k in q for k in ["新老", "新用户", "老用户"]) and any(k in q for k in ["转化率", "转化"]):
        intent = "unknown"
        not_supported = {"metric": "新老用户转化率", "reason": "暂时无法查询", "suggestion": "可查核心指标、漏斗转化"}
    elif ("买家数" in q or "买家多少" in q) and "核心指标" not in q and "指标如何" not in q:
        # 问 PV/UV/买家数 组合时视为核心指标查询，不 not_supported
        # 诊断语境（为什么/原因/变化/下降）：可查 overview/funnel 做分析，不 not_supported
        if any(k in q for k in ["pv", "uv", "PV", "UV"]):
            intent = "overview_day" if dt else "overview_daily"
        elif any(k in q for k in ["为什么", "原因", "怎么回事", "波动", "变化", "下降", "上升", "掉了", "跌"]):
            intent = "diagnose_generic"
        else:
            intent = "unknown"
            not_supported = {"metric": "买家数", "reason": "暂时无法查询", "suggestion": "可查核心指标获取 UV/买家/PV"}
    elif any(k in q for k in ["日活", "dau", "活跃用户", "活跃数", "活跃度", "活跃数据"]):
        intent = "unknown"
        not_supported = {"metric": "日活/活跃度", "reason": "暂时无法查询", "suggestion": "可查核心指标 UV"}
    elif any(k in q for k in ["次日留存", "留存率", "次留"]):
        intent = "unknown"
        not_supported = {"metric": "次日留存", "reason": "暂时无法查询", "suggestion": "可查核心指标、漏斗转化"}
    # 诊断（为什么/原因/怎么回事/波动；下降/掉了 等与类目同时出现时由 _override_complex_to_diagnose 处理）
    elif any(k in q for k in ["为什么", "原因", "怎么回事", "波动"]) or (
        any(k in q for k in ["上升", "下降", "掉了", "跌", "下滑", "变差", "诊断"]) and "类目" not in q
    ):
        intent = "diagnose_generic"
    # 类目/品类（纯类目问题，无诊断关键词）
    elif any(k in q for k in ["哪些类目", "类目贡献", "拖累", "拉动", "类目", "品类", "分类", "top5", "top 5"]):
        intent = "category_contrib_buyers"
    # 留存
    elif any(k in q for k in ["留存", "次留", "次日留存"]):
        intent = "user_retention"
    # 日活
    elif any(k in q for k in ["日活", "dau", "活跃用户", "活跃数", "活跃数据"]):
        intent = "user_activity"
    # 新老转化
    elif any(k in q for k in ["新老", "新用户", "老用户", "新老差异", "新老对比"]):
        intent = "new_vs_old_user_conversion"
    # 漏斗/转化
    elif any(k in q for k in ["漏斗", "转化链", "加购到购买", "uv到购买", "加购率"]):
        intent = "funnel_daily"
    elif "转化" in q and "新老" not in q:
        intent = "funnel_daily"  # 问转化/转化率一律用 funnel（含 uv_to_buyer/uv_to_cart/cart_to_buyer）
    elif "转化率" in q and "新老" not in q:
        intent = "funnel_daily"
    # 单日指标（含模糊：数据、怎么样、看下、查下）
    elif any(k in q for k in ["核心指标", "指标如何", "数据怎么样", "数据情况", "表现如何", "看下数据", "查下", "帮我看看", "uv多少", "买家多少", "pv多少"]):
        intent = "overview_day" if dt else "overview_daily"
    elif any(k in q for k in ["pv", "uv", "PV", "UV"]) and (dt or "日" in q or "月" in q):
        intent = "overview_day" if dt else "overview_daily"  # 显式问 PV/UV/买家数
    elif any(k in q for k in ["趋势", "最近", "过去", "近", "走势", "整体情况", "整体表现"]):
        intent = "funnel_daily" if ("漏斗" in q or "转化" in q) else "overview_daily"
    # 纯模糊：数据、怎么样、如何（无关键词时）
    elif any(k in q for k in ["数据", "怎么样", "如何", "看一下"]) and len(q) <= 25:
        intent = "overview_day" if dt else "overview_daily"

    # diagnose_generic：有 prev_dt+dt 时用两日分析；无 dt 时补 days=9
    if intent == "diagnose_generic" and dt is None and days is None:
        days = 9
        assumptions.append("dt 缺失，days 默认 9 供 funnel 诊断")

    out = {
        "intent": intent,
        "dt": dt,
        "days": days,
        "assumptions": assumptions,
        "not_supported": not_supported,
    }
    if prev_dt is not None:
        out["prev_dt"] = prev_dt
    return out


def _question_specifies_metric(question: str) -> bool:
    """问题里是否显式指定了诊断对象（买家/UV/转化等）。"""
    q = (question or "").strip()
    return any(k in q for k in ["买家", "uv", "UV", "转化率", "转化", "加购"])


def _override_complex_to_diagnose(result: dict, question: str) -> None:
    """诊断+类目混杂：如「X掉了，哪个类目拖累的」既问现象又问类目，强制 diagnose_generic。不含「哪些类目导致」型纯类目问法。"""
    if result.get("not_supported"):
        return
    q = (question or "").strip()
    # 「掉了/跌了」+「哪个类目/拖累」→ 诊断+类目；「哪些类目导致」为主问时保持 category
    has_observation = any(k in q for k in ["掉了", "跌了", "为什么", "原因", "怎么回事"])
    has_category_ask = any(k in q for k in ["哪个类目", "哪些类目", "拖累"])
    is_pure_category = "哪些类目" in q and "导致" in q and not any(k in q for k in ["为什么", "原因", "怎么回事", "掉了", "跌了"])
    if has_observation and has_category_ask and not is_pure_category and result.get("intent") == "category_contrib_buyers":
        result["intent"] = "diagnose_generic"
        if result.get("days") is None:
            result["days"] = 9


def _override_not_supported_if_needed(result: dict, question: str) -> None:
    """暂时无法查询：命中时强制覆盖 intent 与 not_supported。"""
    q = (question or "").lower().strip()
    if any(k in q for k in ["新老", "新用户", "老用户"]) and any(k in q for k in ["转化率", "转化"]):
        result["intent"] = "unknown"
        result["not_supported"] = {"metric": "新老用户转化率", "reason": "暂时无法查询", "suggestion": "可查核心指标、漏斗转化"}
    elif ("买家数" in q or "买家多少" in q) and "核心指标" not in q and "指标如何" not in q:
        if any(k in q for k in ["pv", "uv", "PV", "UV"]):
            result["intent"] = "overview_day" if result.get("dt") else "overview_daily"
        else:
            result["intent"] = "unknown"
            result["not_supported"] = {"metric": "买家数", "reason": "暂时无法查询", "suggestion": "可查核心指标获取 UV/买家/PV"}
    elif any(k in q for k in ["日活", "dau", "活跃用户", "活跃数", "活跃度", "活跃数据"]):
        result["intent"] = "unknown"
        result["not_supported"] = {"metric": "日活/活跃度", "reason": "暂时无法查询", "suggestion": "可查核心指标 UV"}
    elif any(k in q for k in ["次日留存", "留存率", "次留"]):
        result["intent"] = "unknown"
        result["not_supported"] = {"metric": "次日留存", "reason": "暂时无法查询", "suggestion": "可查核心指标、漏斗转化"}


def _apply_session_ctx(result: dict, question: str, session_ctx: dict | None) -> None:
    """
    当 dt/days/metric_focus 缺失时，用 session_ctx 补全，并写入 assumptions。
    就地修改 result。
    """
    session_ctx = session_ctx if isinstance(session_ctx, dict) else {}
    assumptions = list(result.get("assumptions") or [])

    # 1. dt：问题无日期 且 session 有 last_dt
    if result.get("dt") is None and session_ctx.get("last_dt"):
        result["dt"] = session_ctx["last_dt"]
        assumptions.append(f"未指定日期，沿用上一轮 dt={result['dt']}")
    # 1b. prev_dt：连续单日后的对比基准（供「为什么上升/下降」拆解）；问题已含范围时保留
    if result.get("prev_dt") is None and session_ctx.get("prev_dt"):
        result["prev_dt"] = session_ctx["prev_dt"]

    # 2. days：问题无 days 且意图需 days；优先 session.last_days，否则 diagnose 默认 9
    # 有 prev_dt 时用两日对比，不补 days=9
    if result.get("days") is None and result.get("intent") in INTENTS_NEED_DAYS:
        if result.get("prev_dt"):
            pass  # 两日诊断，不补 days
        elif session_ctx.get("last_days") is not None:
            d = int(session_ctx["last_days"])
            result["days"] = max(1, min(90, d))
            assumptions.append(f"未指定 days，沿用上一轮 days={result['days']}")
        elif result.get("intent") == "diagnose_generic":
            result["days"] = 9
            assumptions.append("days 默认 9 供 funnel 诊断")

    # 3. metric_focus：diagnose 且未指定对象，优先从 last_answer_summary 推断（上升/下降追问）
    if result.get("intent") == "diagnose_generic" and not _question_specifies_metric(question):
        prev = session_ctx.get("last_metric_focus")
        summary = (session_ctx.get("last_answer_summary") or "").lower()
        if "上升" in question or "下降" in question:
            if "uv" in summary or "访客" in summary:
                prev = prev or "uv"
            elif "买家" in summary:
                prev = prev or "buyers"
        focus = prev if prev else DEFAULT_METRIC_FOCUS
        result["metric_focus"] = focus
        if prev:
            assumptions.append(f"未指定诊断对象，沿用 metric_focus={focus}")
        else:
            assumptions.append(f"未指定诊断对象，默认 metric_focus={focus}")

    result["assumptions"] = assumptions


def map_query(question: str, session_ctx: dict | None = None) -> dict[str, Any]:
    """
    调用 LLM，将用户问题映射为结构化 JSON。
    session_ctx 可选，用于记忆补全：缺 dt/days/metric_focus 时沿用上一轮。

    返回字段：
    - intent: overview_day | overview_daily | funnel_daily | category_contrib_buyers |
              user_retention | user_activity | new_vs_old_user_conversion |
              diagnose_generic | unknown
    - dt: YYYY-MM-DD 或 null
    - days: 1-90 或 null
    - metric_focus: 仅 diagnose_generic，如 uv_to_buyer（缺时由 session_ctx 或默认补全）
    - assumptions: list[str]（含“沿用上一轮”等记忆补全说明）
    - not_supported: null 或 {metric, reason, missing_fields}

    不输出 tool_key/calls。

    Doctest 示例（输入 -> 期望输出）：
    - "2017-12-03 核心指标如何？" -> intent=overview_day, dt=2017-12-03, days=null
    - "12月3日 哪些类目导致买家下降？" -> intent=category_contrib_buyers, dt=2017-12-03, assumptions=["日期无年份，默认 2017 年"]
    - "最近9天核心指标趋势" -> intent=overview_daily, dt=null, days=9
    - "GMV 多少？" -> intent=unknown, not_supported={metric, reason, missing_fields}
    - "最近7天留存怎么样" -> intent=user_retention, dt=null, days=7
    """
    question = (question or "").strip()
    if not question:
        return {
            "intent": "unknown",
            "dt": None,
            "days": None,
            "metric_focus": None,
            "assumptions": [],
            "not_supported": None,
        }

    text = _call_llm(question)
    obj = _parse_json(text) if text else None

    if obj is None or not isinstance(obj, dict):
        out = _fallback_map(question)
        _override_complex_to_diagnose(out, question)
        _apply_session_ctx(out, question, session_ctx)
        return out

    intent = obj.get("intent")
    if intent not in INTENTS:
        intent = "unknown"

    dt = obj.get("dt")
    if dt is not None and not isinstance(dt, str):
        dt = str(dt) if dt else None
    if dt == "" or dt == "null":
        dt = None

    days = obj.get("days")
    if days is not None:
        try:
            days = int(days)
            days = max(1, min(90, days))
        except (ValueError, TypeError):
            days = None

    assumptions = obj.get("assumptions")
    if not isinstance(assumptions, list):
        assumptions = []

    not_supported = obj.get("not_supported")
    if not_supported is not None and not isinstance(not_supported, dict):
        not_supported = None

    out = {
        "intent": intent,
        "dt": dt,
        "days": days,
        "assumptions": assumptions,
        "not_supported": not_supported,
    }
    # 日期范围（12月1日到2日）：LLM 可能未返回 prev_dt，用规则补全
    if out.get("prev_dt") is None:
        n = _fallback_normalize(question)
        if n.get("prev_dt"):
            out["prev_dt"] = n["prev_dt"]
            if "日期无年份，默认 2017 年" in n.get("assumptions", []) and "日期无年份" not in str(assumptions):
                assumptions = list(assumptions) + ["日期无年份，默认 2017 年"]
            out["assumptions"] = assumptions
    # 暂时无法查询：即使 LLM 返回了 intent，也强制覆盖为 not_supported
    _override_not_supported_if_needed(out, question)
    # 复杂问题：诊断+类目混杂时强制 diagnose_generic
    _override_complex_to_diagnose(out, question)
    _apply_session_ctx(out, question, session_ctx)
    out.setdefault("metric_focus", None)
    return out


def main():
    """简单测试（LLM 未配置时走 fallback）。"""
    for q in [
        "2017-12-03 核心指标如何？",
        "12月3日 哪些类目导致买家下降？",
        "最近9天核心指标趋势",
        "GMV 多少？",
        "最近7天留存怎么样",
    ]:
        print(f"Q: {q}")
        print(f"  {map_query(q)}")
        print()


if __name__ == "__main__":
    main()
