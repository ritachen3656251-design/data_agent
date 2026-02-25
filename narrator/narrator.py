# narrator.py
# 将 plan + results 转为可读文本
# answer_obj 是后续所有自然语言输出的「唯一事实来源」

from __future__ import annotations

import json
import os
import re
from typing import Any

import pandas as pd

DEBUG_TRACE = os.environ.get("DEBUG_TRACE", "").lower() in ("1", "true", "yes")

try:
    from .analyzer import analyze, analyze_diagnose
except ImportError:
    analyze = None
    analyze_diagnose = None


def _normalize_from_call(from_call: str | None) -> str | None:
    """将 from_call 转为 results 的 key，支持 '0' 或 'call_0' 格式。"""
    if from_call is None:
        return None
    s = str(from_call).strip()
    if not s:
        return None
    if s.startswith("call_"):
        s = s[5:]  # "call_0" -> "0"
    return s


def render_plots(plan: dict, results: dict) -> tuple[list[dict], list[str]]:
    """
    按 plan.plots 调用 plot_tools 生成图片 artifact。
    强制要求：每个 plot_spec 必须含 from_call，必须从 results[from_call]["df"] 读取真实 df，
    若 df 为空/不存在则跳过并在 limitations 记录。绝不使用 demo 图替代。
    返回 (charts, plot_limitations)。
    """
    charts: list[dict] = []
    plot_limitations: list[str] = []
    for idx, p in enumerate(plan.get("plots") or []):
        plot_type = p.get("plot_type")
        from_call_raw = p.get("from_call")
        from_call = _normalize_from_call(from_call_raw)
        config = p.get("config") or {}
        if DEBUG_TRACE:
            r = results.get(from_call) if from_call else None
            from_exists = r is not None
            df_shape = getattr(r.get("df"), "shape", None) if r else None
            print(f"[TRACE] plot[{idx}] plot_type={plot_type} from_call={from_call} config={config} from_call_exists={from_exists} df.shape={df_shape}")
        if not plot_type or from_call is None:
            plot_limitations.append(f"plot[{idx}] 缺少 from_call，已跳过")
            continue
        r = results.get(from_call)
        if not r:
            plot_limitations.append(f"plot[{idx}] from_call={from_call} 在 results 中不存在，已跳过")
            continue
        df = r.get("df")
        if df is None or (hasattr(df, "empty") and df.empty):
            plot_limitations.append(f"plot[{idx}] from_call={from_call} 的 df 为空或不存在，已跳过")
            continue
        try:
            from tools.plot_tools import plot_trend, plot_topn_bar
            if plot_type == "trend":
                path = plot_trend(df, **{k: v for k, v in config.items() if k in ("x", "ys", "title")})
            elif plot_type == "topn_bar":
                path = plot_topn_bar(df, **{k: v for k, v in config.items() if k in ("x", "y", "n", "title")})
            else:
                plot_limitations.append(f"plot[{idx}] 未知 plot_type={plot_type}，已跳过")
                continue
            charts.append({"path": path, "plot_type": plot_type, "from_call": from_call})
        except (ValueError, Exception) as e:
            plot_limitations.append(f"plot[{idx}] from_call={from_call} 绘图失败：{e}")
    return charts, plot_limitations

# 轻量数字提取：整数、小数、百分比中的数字
_NUM_PAT = re.compile(r"\d+\.?\d*|\.\d+")


# answer_obj 规范字段（固定包含）
# - headline: str 一句话结论
# - evidence: list[{label, value, source}] 至少2条，value 来自 df 或 df 计算
# - analysis_notes: list[str] 做了哪些比较/计算
# - assumptions: list[str] 数据不足时的推断
# - limitations: list[str] 不支持/缺字段/查询失败
# - next_actions: list[{suggestion, tool_key}] 建议下一步问什么 + 对应 tool_key


def _ensure_answer_obj(obj: dict, tool_key: str = "") -> dict:
    """补齐 answer_obj 固定字段，保证类型一致。"""
    out = {
        "headline": obj.get("headline", ""),
        "evidence": obj.get("evidence", []) or [],
        "analysis_notes": obj.get("analysis_notes", []),
        "assumptions": obj.get("assumptions", []),
        "limitations": _to_list(obj.get("limitations")),
        "next_actions": _normalize_next_actions(obj.get("next_actions", [])),
        "tool_key": obj.get("tool_key", tool_key),
    }
    if "insights" in obj:
        out["insights"] = obj["insights"]
    return out


def _to_list(x: Any) -> list:
    """将 limitations 等转为 list。"""
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x]
    return [str(x)]


def _normalize_next_actions(actions: Any) -> list[dict]:
    """将 next_actions 转为 [{suggestion, tool_key}]。"""
    out = []
    for a in (actions or []):
        if isinstance(a, dict):
            out.append({
                "suggestion": a.get("suggestion", str(a.get("tool_key", ""))),
                "tool_key": a.get("tool_key", ""),
            })
        else:
            out.append({"suggestion": str(a), "tool_key": ""})
    return out


def _json_val(val: Any) -> Any:
    """转为 JSON 可序列化的 Python 原生类型。"""
    if hasattr(val, "item"):
        return val.item()
    if isinstance(val, (int, float, str, bool)) or val is None:
        return val
    return val


def _pct_change(a: float, b: float) -> str:
    """计算 a->b 的变化率，基于 df 数字。"""
    if a == 0 or a is None or pd.isna(a):
        return "N/A" if (b == 0 or pd.isna(b)) else "+100%"
    pct = (float(b) - float(a)) / float(a) * 100
    return f"{pct:+.1f}%"


def _to_results_dict(plan: dict, results: Any) -> dict:
    """将 list 或 dict 形式的 results 统一为 dict 格式。"""
    if isinstance(results, dict):
        return results
    calls = plan.get("calls") or []
    out = {}
    for i, (c, val) in enumerate(zip(calls, results) if isinstance(results, (list, tuple)) else []):
        tool = c.get("tool") or c.get("tool_key")
        params = c.get("params") or {}
        if isinstance(val, pd.DataFrame):
            ok = not val.empty
            out[str(i)] = {"tool_key": tool, "params": params, "ok": ok, "df": val, "error": None if ok else "空数据"}
        else:
            out[str(i)] = {"tool_key": tool, "params": params, "ok": False, "df": None, "error": str(val) if val is not None else "无数据"}
    return out


def _build_not_supported_obj(plan: dict) -> dict:
    """not_supported 分支。"""
    ns = plan.get("not_supported") or {}
    metric = ns.get("metric", "该指标")
    reason = ns.get("reason", "当前数据无法支持")
    missing = ns.get("missing_fields", "")
    limitations = [reason]
    if missing:
        limitations.append(f"缺字段：{missing}" if isinstance(missing, str) else f"缺字段：{', '.join(missing)}")
    return _ensure_answer_obj({
        "headline": f"当前数据无法回答 {metric}",
        "evidence": [],  # not_supported 无 df
        "limitations": limitations,
        "next_actions": [
            {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
            {"suggestion": "某天漏斗表现？", "tool_key": "funnel_daily"},
            {"suggestion": "某天哪些类目导致买家变化？", "tool_key": "category_contrib_buyers"},
        ],
        "tool_key": "not_supported",
    }, "not_supported")


def _build_overview_day_obj(df: pd.DataFrame, question: str = "") -> dict:
    """overview_day：单日核心指标。question 用于显式问 PV/UV/买家数 时定制 headline 与 evidence 顺序。"""
    row = df.iloc[0]
    dt_val = str(row.get("dt", ""))[:10] if pd.notna(row.get("dt")) else ""
    q = (question or "").lower()
    ask_pv_uv_buyers = (
        any(k in q for k in ["pv", "uv"]) and "买家" in q
    ) or ("/" in question and any(k in q for k in ["pv", "uv", "买家"])) or ("、" in question and any(k in q for k in ["pv", "uv", "买家"]))
    if ask_pv_uv_buyers:
        cols_priority = [
            ("pv", "PV"),
            ("uv", "UV"),
            ("buyers", "买家数"),
            ("cart_users", "加购用户数"),
        ]
        headline = f"{dt_val} PV、UV、买家数"
    else:
        cols_priority = [
            ("uv", "UV"),
            ("buyers", "买家数"),
            ("uv_to_buyer", "UV 到购买转化率"),
            ("pv", "PV"),
            ("cart_users", "加购用户数"),
            ("uv_to_cart", "加购率"),
            ("cart_to_buyer", "加购到购买转化率"),
        ]
        headline = f"{dt_val} 核心指标汇总如下"
    evidence = []
    added = set()
    for col, label in cols_priority:
        if col not in df.columns or col in added:
            continue
        val = row.get(col)
        if pd.isna(val):
            continue
        evidence.append({"label": label, "value": _json_val(val), "source": col})
        added.add(col)
        if len(evidence) >= 3:
            break
    for col, label in cols_priority:
        if len(evidence) >= 3:
            break
        if col not in df.columns or col in added:
            continue
        val = row.get(col)
        if pd.notna(val):
            evidence.append({"label": label, "value": _json_val(val), "source": col})
            added.add(col)
    # 转化率列格式化为百分比（value 仍来自 df）
    for e in evidence:
        src = e.get("source", "")
        v = e.get("value")
        if src in ("uv_to_buyer", "uv_to_cart", "cart_to_buyer") and isinstance(v, (int, float)) and 0 <= v <= 1:
            e["value"] = f"{v:.2%}"

    return _ensure_answer_obj({
        "headline": headline,
        "evidence": evidence,
        "analysis_notes": [f"取 {dt_val} 单日 df 行，按列优先序展示 uv/buyers/uv_to_buyer 等"],
        "next_actions": [
            {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
            {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
        ],
        "tool_key": "overview_day",
    }, "overview_day")


def _build_overview_daily_obj(df: pd.DataFrame, params: dict) -> dict:
    """overview_daily：最近 N 天趋势，earliest vs latest。"""
    if len(df) < 2:
        if len(df) == 1:
            row = df.iloc[0]
            evidence = []
            for col, label in [("uv", "UV"), ("buyers", "买家数"), ("pv", "PV")]:
                if col in df.columns and pd.notna(row.get(col)):
                    evidence.append({"label": label, "value": _json_val(row[col]), "source": col})
            return _ensure_answer_obj({
                "headline": f"仅有一天数据（{str(row.get('dt',''))[:10]}）",
                "evidence": evidence[:3],
                "limitations": ["需至少 2 天才能计算趋势"],
                "next_actions": [
                    {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
                    {"suggestion": "指定某天为什么转化下降？", "tool_key": "diagnose_generic"},
                ],
                "tool_key": "overview_daily",
            }, "overview_daily")
        earliest = latest = {}
    else:
        latest = df.iloc[0]
        earliest = df.iloc[-1]
    days = params.get("days", 9)

    def _v(r: Any, col: str) -> float:
        v = r.get(col) if hasattr(r, "get") else None
        if pd.isna(v):
            return 0.0
        return float(v)

    evidence = []
    # UV
    ev = _v(earliest, "uv")
    lv = _v(latest, "uv")
    if "uv" in df.columns:
        evidence.append({"label": "UV", "value": f"{int(ev)} -> {int(lv)}", "change": _pct_change(ev, lv), "source": "uv"})
    eb = _v(earliest, "buyers")
    lb = _v(latest, "buyers")
    if "buyers" in df.columns:
        evidence.append({"label": "买家数", "value": f"{int(eb)} -> {int(lb)}", "change": _pct_change(eb, lb), "source": "buyers"})
    ep = _v(earliest, "pv")
    lp = _v(latest, "pv")
    if "pv" in df.columns and len(evidence) < 3:
        evidence.append({"label": "PV", "value": f"{int(ep)} -> {int(lp)}", "change": _pct_change(ep, lp), "source": "pv"})

    uv_up = lv >= ev if ev else True
    buyers_up = lb >= eb if eb else True
    trend = "整体上升" if (uv_up and buyers_up) else ("整体下降" if (not uv_up and not buyers_up) else "UV 与买家趋势不一")

    insights = analyze(df, "overview_daily") if analyze else []
    return _ensure_answer_obj({
        "headline": f"最近{days}天趋势：UV/买家{trend}",
        "evidence": evidence,
        "insights": insights,
        "analysis_notes": [f"比较 df 首行(latest)与末行(earliest)，计算各列变化率"],
        "next_actions": [
            {"suggestion": f"最近{days}天漏斗表现？", "tool_key": "funnel_daily"},
            {"suggestion": "指定某天为什么转化下降？", "tool_key": "diagnose_generic"},
        ],
        "tool_key": "overview_daily",
    }, "overview_daily")


def _build_funnel_daily_obj(df: pd.DataFrame, params: dict) -> dict:
    """funnel_daily：漏斗转化率首尾对比，主结论取变化幅度最大的一段。"""
    if len(df) < 2:
        if len(df) == 1:
            row = df.iloc[0]
            evidence = []
            for col, label in [("uv_to_buyer", "UV 到购买转化率"), ("uv_to_cart", "加购率"), ("cart_to_buyer", "加购到购买转化率")]:
                if col in df.columns and pd.notna(row.get(col)):
                    v = row[col]
                    evidence.append({"label": label, "value": f"{v:.2%}" if v < 1 else str(v), "source": col})
            return _ensure_answer_obj({
                "headline": f"仅有一天漏斗数据（{str(row.get('dt',''))[:10]}）",
                "evidence": evidence[:3],
                "limitations": ["需至少 2 天才能计算趋势"],
                "next_actions": [
                    {"suggestion": "某天核心指标如何？", "tool_key": "overview_day"},
                    {"suggestion": "某天哪些类目导致买家变化？", "tool_key": "category_contrib_buyers"},
                ],
                "tool_key": "funnel_daily",
            }, "funnel_daily")
        earliest = latest = {}
    else:
        latest = df.iloc[0]
        earliest = df.iloc[-1]

    def _v(r: Any, col: str) -> float:
        v = r.get(col) if hasattr(r, "get") else None
        if pd.isna(v):
            return 0.0
        return float(v)

    cols = [
        ("uv_to_buyer", "UV 到购买转化率"),
        ("uv_to_cart", "加购率"),
        ("cart_to_buyer", "加购到购买转化率"),
    ]
    evidence = []
    changes = []
    for col, label in cols:
        if col not in df.columns:
            continue
        ev = _v(earliest, col)
        lv = _v(latest, col)
        ch = _pct_change(ev, lv)
        val_str = f"{ev:.2%} -> {lv:.2%}" if ev < 1 or lv < 1 else f"{ev:.2f} -> {lv:.2f}"
        evidence.append({"label": label, "value": val_str, "change": ch, "source": col})
        try:
            pct = (lv - ev) / ev * 100 if ev else 0
            changes.append((abs(pct), label, col))
        except Exception:
            pass

    headline = f"漏斗变化主要发生在{changes[0][1]}段" if changes else "最近漏斗转化率汇总"
    notes = [f"比较首末行 uv_to_buyer/uv_to_cart/cart_to_buyer 变化，取变化幅度最大的一段"] if changes else ["展示各转化率首尾对比"]

    insights = analyze(df, "funnel_daily") if analyze else []
    return _ensure_answer_obj({
        "headline": headline,
        "evidence": evidence,
        "insights": insights,
        "analysis_notes": notes,
        "next_actions": [
            {"suggestion": "某天核心指标如何？", "tool_key": "overview_day"},
            {"suggestion": "某天哪些类目导致买家变化？", "tool_key": "category_contrib_buyers"},
        ],
        "tool_key": "funnel_daily",
    }, "funnel_daily")


def _build_category_contrib_buyers_obj(df: pd.DataFrame, params: dict) -> dict:
    """category_contrib_buyers：类目贡献 TopN，按 delta DESC。"""
    if df.empty or "delta" not in df.columns:
        return _ensure_answer_obj({
            "headline": "无类目贡献数据",
            "evidence": [],
            "limitations": ["该日期无类目数据或未落库"],
            "next_actions": [
                {"suggestion": "该天漏斗表现？", "tool_key": "funnel_daily"},
                {"suggestion": "该天新老用户转化差异？", "tool_key": "new_vs_old_user_conversion"},
            ],
            "tool_key": "category_contrib_buyers",
        }, "category_contrib_buyers")
    dt_val = str(params.get("dt", ""))[:10] or "该天"
    q = str(params.get("_question", ""))
    if "下降" in q:
        sub = df[df["delta"] < 0].copy()
        sub = sub.sort_values("delta", ascending=True).head(5)
        top1_delta_neg = True
    else:
        sub = df.head(5)
        top1_delta_neg = len(sub) > 0 and sub.iloc[0].get("delta", 0) < 0
    if sub.empty:
        top1_delta_neg = False
        sub = df.head(5)
    top1 = sub.iloc[0] if len(sub) > 0 else None
    top1_cat = _json_val(top1.get("category_id")) if top1 is not None else ""
    top1_delta = _json_val(top1.get("delta")) if top1 is not None else 0

    evidence = []
    evidence.append({"label": "Top 类目贡献", "value": f"category_id={top1_cat}，delta={top1_delta}", "source": "delta"})
    for _, r in sub.iterrows():
        cid = _json_val(r.get("category_id"))
        d = _json_val(r.get("delta"))
        evidence.append({"label": f"类目 {cid}", "value": f"delta={d}", "source": "delta"})

    headline = "主要拖累来自以下类目" if top1_delta_neg else "主要拉动来自以下类目"

    insights = analyze(df, "category_contrib_buyers") if analyze else []
    return _ensure_answer_obj({
        "headline": headline,
        "evidence": evidence,
        "insights": insights,
        "analysis_notes": [f"按 delta 排序取 Top5，问题含「下降」时取 delta<0 并按 delta 升序"],
        "next_actions": [
            {"suggestion": f"{dt_val} 漏斗表现？", "tool_key": "funnel_daily"},
            {"suggestion": f"{dt_val} 新老用户转化差异？", "tool_key": "new_vs_old_user_conversion"},
        ],
        "tool_key": "category_contrib_buyers",
    }, "category_contrib_buyers")


def _build_user_retention_obj(df: pd.DataFrame, params: dict) -> dict:
    """user_retention：dt DESC，earliest vs latest + 最新值。"""
    if df.empty or "retention_1d" not in df.columns:
        return _ensure_answer_obj({
            "headline": "无留存数据",
            "evidence": [],
            "limitations": ["该日期无留存数据"],
            "next_actions": [
                {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
                {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
            ],
            "tool_key": "user_retention",
        }, "user_retention")
    if len(df) >= 2:
        latest = df.iloc[0]
        earliest = df.iloc[-1]
        lv = float(latest.get("retention_1d", 0) or 0)
        ev = float(earliest.get("retention_1d", 0) or 0)
        evidence = [
            {"label": "留存率对比", "value": f"{ev:.2%} -> {lv:.2%}", "change": _pct_change(ev, lv), "source": "retention_1d"},
            {"label": "最新留存率", "value": f"{lv:.2%}", "source": "retention_1d"},
        ]
        trend = "上升" if lv >= ev else "下降"
        headline = f"留存近期走势{trend}"
        notes = ["比较首末行 retention_1d，计算变化率"]
    else:
        row = df.iloc[0]
        lv = float(row.get("retention_1d", 0) or 0)
        evidence = [{"label": "最新留存率", "value": f"{lv:.2%}", "source": "retention_1d"}]
        headline = "留存数据（仅一天）"
        notes = ["仅一天数据，无趋势比较"]

    insights = analyze(df, "user_retention") if analyze else []
    return _ensure_answer_obj({
        "headline": headline,
        "evidence": evidence,
        "insights": insights,
        "analysis_notes": notes,
        "next_actions": [
            {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
            {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
        ],
        "tool_key": "user_retention",
    }, "user_retention")


def _build_new_vs_old_user_conversion_obj(df: pd.DataFrame, params: dict) -> dict:
    """new_vs_old_user_conversion：某日新老用户转化率。"""
    if df.empty or "new_cvr" not in df.columns:
        return _ensure_answer_obj({
            "headline": "无新老转化数据",
            "evidence": [],
            "limitations": ["该日期无新老转化数据"],
            "next_actions": [
                {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
                {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
            ],
            "tool_key": "new_vs_old_user_conversion",
        }, "new_vs_old_user_conversion")
    row = df.iloc[0]
    dt_val = str(params.get("dt", ""))[:10] or str(row.get("dt", ""))[:10]
    new_cvr = float(row.get("new_cvr", 0) or 0)
    old_cvr = float(row.get("old_cvr", 0) or 0)
    new_uv = int(row.get("new_uv", 0) or 0)
    old_uv = int(row.get("old_uv", 0) or 0)
    new_buyers = int(row.get("new_buyers", 0) or 0)
    old_buyers = int(row.get("old_buyers", 0) or 0)
    evidence = [
        {"label": "新用户转化率", "value": f"{new_cvr:.2%}", "source": "new_cvr"},
        {"label": "老用户转化率", "value": f"{old_cvr:.2%}", "source": "old_cvr"},
        {"label": "新用户 UV/买家", "value": f"{new_uv} / {new_buyers}", "source": "new_uv,new_buyers"},
        {"label": "老用户 UV/买家", "value": f"{old_uv} / {old_buyers}", "source": "old_uv,old_buyers"},
    ]
    return _ensure_answer_obj({
        "headline": f"{dt_val} 新老用户转化率",
        "evidence": evidence[:4],
        "analysis_notes": [f"取 {dt_val} 单日 new_cvr/old_cvr 及 new_uv/old_uv/new_buyers/old_buyers"],
        "next_actions": [
            {"suggestion": f"{dt_val} 漏斗表现？", "tool_key": "funnel_daily"},
            {"suggestion": f"{dt_val} 类目贡献？", "tool_key": "category_contrib_buyers"},
        ],
        "tool_key": "new_vs_old_user_conversion",
    }, "new_vs_old_user_conversion")


def _build_user_activity_obj(df: pd.DataFrame, params: dict) -> dict:
    """user_activity：dt DESC，earliest vs latest + 最新值。"""
    if df.empty or "dau" not in df.columns:
        return _ensure_answer_obj({
            "headline": "无活跃数据",
            "evidence": [],
            "limitations": ["该日期无活跃数据"],
            "next_actions": [
                {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
                {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
            ],
            "tool_key": "user_activity",
        }, "user_activity")
    if len(df) >= 2:
        latest = df.iloc[0]
        earliest = df.iloc[-1]
        lv = float(latest.get("dau", 0) or 0)
        ev = float(earliest.get("dau", 0) or 0)
        evidence = [
            {"label": "DAU 对比", "value": f"{int(ev)} -> {int(lv)}", "change": _pct_change(ev, lv), "source": "dau"},
            {"label": "最新 DAU", "value": int(lv), "source": "dau"},
        ]
        trend = "上升" if lv >= ev else "下降"
        headline = f"活跃近期走势{trend}"
        notes = ["比较首末行 dau，计算变化率"]
    else:
        row = df.iloc[0]
        lv = int(row.get("dau", 0) or 0)
        evidence = [{"label": "最新 DAU", "value": lv, "source": "dau"}]
        headline = "活跃数据（仅一天）"
        notes = ["仅一天数据，无趋势比较"]

    insights = analyze(df, "user_activity") if analyze else []
    return _ensure_answer_obj({
        "headline": headline,
        "evidence": evidence,
        "insights": insights,
        "analysis_notes": notes,
        "next_actions": [
            {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
            {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
        ],
        "tool_key": "user_activity",
    }, "user_activity")


def build_answer_obj(question: str, plan: dict, results: Any) -> dict:
    """
    构建结构化回答对象。
    支持：not_supported、overview_day、overview_daily、funnel_daily。
    """
    # 1. not_supported 优先
    if plan.get("not_supported"):
        return _build_not_supported_obj(plan)

    rd = _to_results_dict(plan, results)
    call_keys = {c.get("tool_key") or c.get("tool") for c in (plan.get("calls") or [])}
    is_diagnose_plan = "overview_day" in call_keys and "funnel_daily" in call_keys

    # 2. diagnose 证据链：合并 overview_day + funnel_daily（+ category）的 evidence 与 insights
    # 有双日对比时（两日 overview_day），用 _build_overview_daily_obj 做 prev vs curr 拆解
    if is_diagnose_plan:
        merged_evidence = []
        merged_notes = ["compare overview_day + funnel_daily for diagnose"]
        merged_insights = []
        overview_dfs = []
        overview_combined: pd.DataFrame | None = None
        funnel_df: pd.DataFrame | None = None
        for rv in rd.values():
            if (rv.get("tool_key") or rv.get("tool")) == "overview_day":
                df = rv.get("df")
                if df is not None and not (hasattr(df, "empty") and df.empty):
                    overview_dfs.append(df)
        if len(overview_dfs) >= 2:
            df_combined = pd.concat(overview_dfs, ignore_index=True).drop_duplicates(subset=["dt"] if "dt" in (overview_dfs[0].columns if overview_dfs else []) else [])
            if "dt" in df_combined.columns:
                df_combined = df_combined.sort_values("dt", ascending=False).reset_index(drop=True)
            overview_combined = df_combined
            o = _build_overview_daily_obj(df_combined, {"days": len(df_combined)})
            merged_evidence.extend(o.get("evidence", [])[:4])
            merged_notes.extend(o.get("analysis_notes", []))
            merged_insights.extend(o.get("insights", []) or [])
        elif len(overview_dfs) == 1:
            overview_combined = overview_dfs[0]
            o = _build_overview_day_obj(overview_dfs[0])
            merged_evidence.extend(o.get("evidence", [])[:3])
            merged_notes.extend(o.get("analysis_notes", []))
        for tk in ("funnel_daily", "category_contrib_buyers"):
            if tk not in call_keys:
                continue
            v = None
            for rv in rd.values():
                if (rv.get("tool_key") or rv.get("tool")) == tk:
                    v = rv
                    break
            if not v:
                continue
            df = v.get("df")
            if df is None or (hasattr(df, "empty") and df.empty):
                continue
            if tk == "funnel_daily":
                funnel_df = df
                o = _build_funnel_daily_obj(df, v.get("params", {}))
            else:
                o = _build_category_contrib_buyers_obj(df, dict(v.get("params", {}), _question=question))
            merged_evidence.extend(o.get("evidence", [])[:3])
            merged_notes.extend(o.get("analysis_notes", []))
            merged_insights.extend(o.get("insights", []) or [])
        # 诊断专用结构化 insights：主因+次要变化+关键指标，供「最可能的原因是…」式回答
        if analyze_diagnose and overview_combined is not None and funnel_df is not None:
            diag_insights = analyze_diagnose(overview_combined, funnel_df)
            if diag_insights:
                merged_insights = diag_insights
        if merged_evidence:
            plan_assumptions = plan.get("assumptions") or []
            primary = next((i for i in merged_insights if i.get("type") == "diagnose_primary"), None)
            headline = primary.get("text", "诊断结论（基于单日指标与漏斗对比）") if primary else "诊断结论（基于单日指标与漏斗对比）"
            obj = _ensure_answer_obj({
                "headline": headline,
                "evidence": merged_evidence[:6],
                "insights": merged_insights,
                "analysis_notes": merged_notes,
                "assumptions": plan_assumptions,
                "limitations": [],
                "next_actions": [
                    {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
                    {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
                    {"suggestion": "某天哪些类目导致变化？", "tool_key": "category_contrib_buyers"},
                ],
                "tool_key": "diagnose_generic",
            })
            if plan_assumptions:
                obj["assumptions"] = list(obj.get("assumptions", [])) + [str(a) for a in plan_assumptions if a]
            return obj

    # 3. 按工具类型查找并处理
    tool_handlers = [
        ("overview_day", _build_overview_day_obj, lambda v: (v.get("df"), question)),
        ("overview_daily", _build_overview_daily_obj, lambda v: (v.get("df"), v.get("params", {}))),
        ("funnel_daily", _build_funnel_daily_obj, lambda v: (v.get("df"), v.get("params", {}))),
        ("category_contrib_buyers", _build_category_contrib_buyers_obj, lambda v: (v.get("df"), dict(v.get("params", {}), _question=question))),
        ("user_retention", _build_user_retention_obj, lambda v: (v.get("df"), v.get("params", {}))),
        ("user_activity", _build_user_activity_obj, lambda v: (v.get("df"), v.get("params", {}))),
        ("new_vs_old_user_conversion", _build_new_vs_old_user_conversion_obj, lambda v: (v.get("df"), v.get("params", {}))),
    ]

    for tool_key, handler, get_args in tool_handlers:
        for k, v in rd.items():
            t = v.get("tool_key") or v.get("tool")
            if t != tool_key:
                continue
            df = v.get("df")
            if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                return _ensure_answer_obj({
                    "headline": "",
                    "evidence": [],
                    "limitations": ["该日期无数据或未落库"],
                    "next_actions": [
                        {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
                        {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
                    ],
                })
            if tool_key == "overview_day":
                args = get_args(v)
                obj = handler(args[0], args[1])
            else:
                args = get_args(v)
                obj = handler(args[0], args[1])
            # 注入 plan.assumptions（数据不足时的推断）
            plan_assumptions = plan.get("assumptions") or []
            if plan_assumptions:
                obj["assumptions"] = list(obj.get("assumptions", [])) + [str(a) for a in plan_assumptions if a]
            return obj

    # 未匹配任何工具
    return _ensure_answer_obj({
        "headline": "",
        "evidence": [],
        "limitations": ["暂无对应数据"],
        "next_actions": [
            {"suggestion": "最近9天核心指标趋势？", "tool_key": "overview_daily"},
            {"suggestion": "最近9天漏斗表现？", "tool_key": "funnel_daily"},
        ],
    })


def render_surface(question: str, answer_obj: dict, style: str = "auto") -> str:
    """
    将 answer_obj 渲染为用户可读文本。
    answer_obj 为唯一事实来源；headline/evidence/limitations/next_actions 均取自其中。
    """
    tool_key = answer_obj.get("tool_key", "")
    headline = answer_obj.get("headline", "")
    evidence = answer_obj.get("evidence", [])[:5]
    raw_actions = answer_obj.get("next_actions", [])[:3]
    next_actions = [a.get("suggestion", a) if isinstance(a, dict) else a for a in raw_actions]
    limitations = answer_obj.get("limitations", [])

    parts = []
    # headline 必须
    if headline:
        parts.append(headline)

    # evidence 至少 2 条，value 原样
    if evidence:
        if tool_key in ("category_contrib_buyers",):
            parts.append("")
            for e in evidence[:5]:
                label = e.get("label", "")
                val = e.get("value", "")
                ch = e.get("change", "")
                line = f"• {label}：{val}"
                if ch:
                    line += f"（{ch}）"
                parts.append(line)
        elif tool_key in ("not_supported",):
            pass  # limitations 单独输出
        else:
            parts.append("")
            for e in evidence[:5]:
                label = e.get("label", "")
                val = e.get("value", "")
                ch = e.get("change", "")
                line = f"• {label}：{val}"
                if ch:
                    line += f"（{ch}）"
                parts.append(line)

    # limitations
    if limitations:
        parts.append("")
        lim_str = "；".join(str(x) for x in limitations) if isinstance(limitations, list) else str(limitations)
        parts.append(f"说明：{lim_str}")

    # next_actions
    if next_actions:
        parts.append("")
        actions_str = "；".join(str(a) for a in next_actions[:3])
        parts.append(f"你也可以继续问：{actions_str}")

    return "\n".join(p for p in parts if p is not None)


def _answer_obj_to_natural_language(obj: dict) -> str:
    """兼容旧逻辑，内部调用 render_surface。"""
    return render_surface("", obj, "auto")


def _allowed_numbers_from_answer_obj(obj: dict) -> frozenset:
    """从 answer_obj 提取允许出现的数字集合（轻量，用于 LLM 输出校验）。"""
    allowed = set()
    for e in obj.get("evidence", []) or []:
        for k in ("value", "change"):
            v = e.get(k)
            if v is not None:
                for m in _NUM_PAT.findall(str(v)):
                    try:
                        allowed.add(float(m))
                    except ValueError:
                        allowed.add(m)
    for i in obj.get("insights", []) or []:
        if isinstance(i, dict):
            for k in ("text", "value", "change_pct", "delta", "from_val", "to_val", "uv", "buyers"):
                v = i.get(k)
                if v is not None:
                    for m in _NUM_PAT.findall(str(v)):
                        try:
                            allowed.add(float(m))
                        except ValueError:
                            allowed.add(m)
    for s in (obj.get("headline", ""),) + tuple(obj.get("limitations", []) or []) + tuple(obj.get("assumptions", []) or []):
        if s:
            for m in _NUM_PAT.findall(str(s)):
                try:
                    allowed.add(float(m))
                except ValueError:
                    allowed.add(m)
    for a in obj.get("next_actions", [])[:3]:
        s = a.get("suggestion", a) if isinstance(a, dict) else str(a)
        if s:
            for m in _NUM_PAT.findall(str(s)):
                try:
                    allowed.add(float(m))
                except ValueError:
                    allowed.add(m)
    return frozenset(allowed)


def _has_unknown_numbers(text: str, allowed: frozenset) -> bool:
    """检查 text 中是否出现不在 allowed 中的新数字。轻量。"""
    for m in _NUM_PAT.findall(text):
        try:
            n = float(m)
            if n not in allowed:
                return True
        except ValueError:
            if m not in allowed:
                return True
    return False


# 是否启用 LLM 写作渲染（默认 True，失败则 fallback 规则渲染）
USE_LLM_RENDER = True

# 是否启用 LLM 润色（在规则渲染后的二次润色，与 USE_LLM_RENDER 二选一）
USE_LLM_POLISH = False


def _is_diagnose(plan: dict | None, answer_obj: dict) -> bool:
    """判断是否为诊断类回答（plan 含 diagnose 证据链，或 analysis_notes 含 compare/diagnose）。"""
    if plan:
        keys = {c.get("tool_key") or c.get("tool") for c in (plan.get("calls") or [])}
        if "overview_day" in keys and "funnel_daily" in keys:
            return True
    notes = " ".join(str(x) for x in (answer_obj.get("analysis_notes") or [])).lower()
    return "compare" in notes or "diagnose" in notes or "比较" in notes or "诊断" in notes


def render_with_llm(
    question: str, answer_obj: str | dict, style: str = "auto", plan: dict | None = None
) -> str:
    """
    用 LLM 将 answer_obj 转为自然语言回答。
    输入：question、answer_obj（dict 或 JSON 串）、style、plan（可选，用于识别 diagnose）
    输出：语气自然、重点突出的可读文本。
    diagnose 时采用特殊结构：结论、证据链、合理假设、缺口与建议、下一步。
    强约束：必须含 headline；至少 2 条 evidence.value 原样；不得引入新数字/新事实。
    失败时返回空串，由调用方 fallback。
    """
    try:
        if isinstance(answer_obj, str):
            obj = json.loads(answer_obj)
        else:
            obj = dict(answer_obj)
    except (json.JSONDecodeError, TypeError):
        return ""

    headline = obj.get("headline", "")
    evidence = obj.get("evidence", []) or []
    ev_values = [str(e.get("value", "")) for e in evidence[:5] if e.get("value") is not None]
    insights = obj.get("insights") or []
    limitations = obj.get("limitations", []) or []
    assumptions = obj.get("assumptions", []) or []
    raw_actions = obj.get("next_actions", [])[:3]
    next_actions = [a.get("suggestion", a) if isinstance(a, dict) else str(a) for a in raw_actions]
    limitations = limitations if isinstance(limitations, list) else [str(limitations)] if limitations else []
    assumptions = assumptions if isinstance(assumptions, list) else [str(assumptions)] if assumptions else []
    has_insights = bool(insights and isinstance(insights, list))

    try:
        import dashscope
        from dashscope import Generation
    except ImportError:
        return ""

    is_diagnose = _is_diagnose(plan, obj)

    if is_diagnose:
        constraints = [
            "1. 先做因果推理：结合 insights/evidence 分析主因与次要因素的关系，再给出结论。",
            "2. 结论：以「最可能的原因是…」引出，带关键数字（如 X%→Y%、降幅 Z%），可适度归纳而非机械罗列。",
            "3. 证据链：包含 UV、买家数及漏斗环节变化，用「尽管…但…」「此外…」等自然衔接因果。",
            "4. 合理假设：若 limitations/assumptions 有推断，可自然融入（如「数据缺失可能是…所致」）。",
            "5. 下一步建议：以「你也可以继续问：」引出 next_actions。",
            "6. 数字须来自 answer_obj，可合理归纳表述，但不得编造 answer_obj 中不存在的数据。",
        ]
        structure = """
请基于数据做语义分析后输出（自然衔接，不必机械分段）：
• 因果推理：主因 + 关键数字，次要因素如何作用
• UV、买家数等核心指标
• 其他环节变化及与主因的关系
• 假设与局限（如有）
• 你也可以继续问：…（next_actions）

"""
    else:
        constraints = [
            "1. 包含 headline 的结论，可适当展开或归纳。",
            "2. 引用至少两条 evidence 中的关键数字，可适度改写表述（如「56.49%」可写为「约 56.5%」），核心数据须准确。",
        ]
        if limitations:
            constraints.append("3. 若有 limitations，自然说明数据缺口或无法支持的原因。")
        if assumptions:
            constraints.append("4. 若有 assumptions，自然说明推断依据。")
        constraints.append("5. 以「你也可以继续问：」引出 next_actions，最多 3 条。")
        constraints.append("6. 数字须来自 answer_obj，可合理归纳，不得编造 answer_obj 中不存在的数据。")
        structure = ""

    insights_instruction = ""
    if has_insights:
        insights_texts = [i.get("text", "") for i in insights[:10] if isinstance(i, dict) and i.get("text")]
        constraints.append("7. 若有 insights：融入分析，做因果解读，勿机械逐条复读。")
        constraints.append("8. 风格：现象 → 解读/含义 → 可验证的下一步。")
        insights_instruction = f"""

数据分析 insights（请融入结论与证据链，勿逐条复读）：
{chr(10).join(f"- {t}" for t in insights_texts)}
"""

    prompt = f"""你是一个数据分析助手。根据用户问题和结构化回答对象，输出一段自然、流畅、重点突出的分析回答。
{structure if is_diagnose else ""}
用户问题：{question}

结构化回答对象（JSON）：
{json.dumps(obj, ensure_ascii=False, indent=2)}
{insights_instruction}

指导原则：
{chr(10).join(constraints)}

evidence.value 示例（核心数字须准确）：{ev_values[:5]}
next_actions 建议：{next_actions[:3]}

请直接输出自然语言回答，直接给结论和证据，可适当推理与归纳。"""

    try:
        r = Generation.call(
            model="qwen-max",
            messages=[
                {"role": "system", "content": "你是数据分析助手，基于结构化数据进行分析推理。可适度归纳、解释因果，输出自然流畅的解读。数字须来自数据，不得编造。"},
                {"role": "user", "content": prompt},
            ],
            result_format="message",
        )
        if r.status_code != 200:
            return ""
        text = (r.output.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        if not text:
            return ""

        # 校验：至少 1 条关键 evidence 数字出现在回答中（允许空格、标点差异）
        required_ev = ev_values[:2]
        found = False
        for ev in required_ev:
            if not ev or len(str(ev).strip()) < 2:
                continue
            ev_clean = re.sub(r"[\s,，。%％]", "", str(ev))
            text_clean = re.sub(r"[\s,，。%％]", "", text)
            if ev_clean and ev_clean in text_clean:
                found = True
                break
            # 若 evidence 含多个数字（如 "56.49% -> 53.24%"），任一数字出现即可
            for m in _NUM_PAT.findall(str(ev)):
                if len(m) >= 2 and m in text:
                    found = True
                    break
            if found:
                break
        if required_ev and not found and any(len(str(e).strip()) >= 3 for e in required_ev if e):
            return ""

        # 轻量校验：不得引入 answer_obj 中不存在的新数字
        allowed = _allowed_numbers_from_answer_obj(obj)
        if allowed and _has_unknown_numbers(text, allowed):
            return ""
        return text
    except Exception:
        return ""


def polish_with_llm(question: str, answer_obj: str, draft_text: str) -> str:
    """
    可选：用 LLM 润色 draft_text。
    强约束：必须包含 headline；至少两条 evidence.value 原样；不得引入新数字；不得改变事实。
    失败或检测到新数字时回退 draft_text。
    """
    if not USE_LLM_POLISH:
        return draft_text
    try:
        import re
        import json as _json
        obj = _json.loads(answer_obj) if isinstance(answer_obj, str) else answer_obj
        ev_values = []
        for e in (obj.get("evidence") or [])[:5]:
            v = e.get("value")
            if v is not None:
                ev_values.append(str(v))
        headline = obj.get("headline", "")

        # 尝试调用 LLM（需 dashscope 等）
        try:
            import dashscope
            from dashscope import Generation
        except ImportError:
            return draft_text

        prompt = f"""请润色以下分析回答，使其更自然流畅。强约束：
1. 必须包含 headline：「{headline}」
2. 必须原样包含以下至少两条证据（不得改数字）：{ev_values}
3. 不得引入 answer_obj 未出现的新数字
4. 不得改变事实，只能改措辞和结构

原文：
{draft_text}

润色后（直接输出，不要其他解释）："""

        r = Generation.call(model="qwen-max", prompt=prompt, max_tokens=500)
        if r.status_code != 200:
            return draft_text
        polished = (r.output.get("text") or "").strip()

        # 检测是否引入新数字：原 draft 中的数字应仍在
        for ev in ev_values[:2]:
            if ev not in polished and not re.search(re.escape(ev[:20]), polished):
                return draft_text
        if headline and headline not in polished:
            return draft_text

        return polished
    except Exception:
        return draft_text


def narrate(question: str, plan: dict, results: Any, style: str = "auto") -> tuple[str, list, dict]:
    """
    将用户问题、执行计划与执行结果合成为自然语言回答。
    优先用 render_with_llm（LLM 写作），失败则 fallback 到规则渲染 render_surface。
    diagnose 时 render_with_llm 会采用结论/证据链/假设/缺口/下一步 的结构化风格。
    返回 (text, charts, answer_obj)，charts 为 render_plots 生成的 artifact 引用列表。
    """
    answer_obj = build_answer_obj(question, plan, results)
    charts, plot_limitations = render_plots(plan, results)
    answer_obj["charts"] = charts
    if plot_limitations:
        answer_obj["limitations"] = list(answer_obj.get("limitations") or []) + plot_limitations
    if USE_LLM_RENDER:
        llm_text = render_with_llm(question, answer_obj, style, plan=plan)
        if llm_text:
            return llm_text, charts, answer_obj
    return render_surface(question, answer_obj, style), charts, answer_obj
