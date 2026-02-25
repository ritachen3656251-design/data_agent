# planner.py
# Planner：优先 LLM 生成 plan，失败时规则回退；规则层做安全与合规校验

from __future__ import annotations

import json
import re
from typing import Any

from .plan_validator import (
    TOOL_WHITELIST,
    TOOLS_NEED_DAYS,
    TOOLS_NEED_DT,
    _extract_dates_from_text,
    validate_plan,
)

USE_LLM_PLANNER = True  # 设为 False 可完全走规则

# 需与 tools.TOOL_REGISTRY 对齐
TOOLS_NEED_DAYS = {"overview_daily", "funnel_daily", "user_retention", "user_activity"}
TOOLS_NEED_DT = {"overview_day", "category_contrib_buyers", "new_vs_old_user_conversion"}

DAYS_DEFAULT_OVERVIEW = 9
DAYS_DEFAULT_RETENTION = 7
DAYS_MIN, DAYS_MAX = 1, 90

# 范围词：含此类词时允许使用 overview_daily(days)
RANGE_WORDS_PAT = re.compile(
    r"最近|近\s*\d*\s*天|近几天|趋势|过去|这几天|这周|本周|上周|近[期时日]"
)


def _has_range_words(question: str) -> bool:
    """用户问题是否包含范围词（最近/近N天/趋势/过去/这几天等）。"""
    q = (question or "").strip()
    return bool(RANGE_WORDS_PAT.search(q))


def _get_default_dt_safe() -> str:
    try:
        from tools.db import get_default_dt
        return get_default_dt()
    except Exception:
        return "2017-12-03"


def _clamp_days(d: int) -> int:
    return max(DAYS_MIN, min(DAYS_MAX, int(d)))


def _call_llm_for_plan(question: str, slots: dict) -> dict | None:
    """调用 LLM 生成 plan，返回解析后的 dict 或 None。"""
    try:
        from dashscope import Generation

        from .planner_prompt import get_planner_prompt, get_planner_user_prompt

        sys_prompt = get_planner_prompt()
        user_prompt = get_planner_user_prompt(question, slots)

        r = Generation.call(
            model="qwen-max",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            result_format="message",
        )
        if r.status_code != 200:
            return None
        text = (r.output.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        if not text:
            return None
        # 剥离 markdown 代码块
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
        return json.loads(text)
    except Exception:
        return None


def _normalize_llm_call(c: dict) -> dict:
    """将 LLM 的 call 转为统一格式 {tool_key, tool, params}。"""
    tool = c.get("tool") or c.get("tool_key") or ""
    params = dict(c.get("params") or {})
    return {"tool_key": tool, "tool": tool, "params": params}


def _validate_and_sanitize_llm_plan(
    llm_plan: dict,
    question: str,
    slots: dict,
) -> dict[str, Any] | None:
    """
    校验并 sanitize LLM 输出的 plan。返回合规的 plan 或 None（需回退规则）。
    - 工具白名单、日期覆盖
    - 参数合规：dt/days/prev_dt 与 slots 一致，days 限制 1-90
    """
    if not llm_plan or not isinstance(llm_plan, dict):
        return None

    # not_supported 透传
    if llm_plan.get("not_supported") and isinstance(llm_plan.get("not_supported"), dict):
        return {
            "calls": [],
            "plots": [],
            "assumptions": list(slots.get("assumptions") or []),
            "not_supported": llm_plan["not_supported"],
        }

    calls_raw = llm_plan.get("calls")
    if not isinstance(calls_raw, list):
        return None

    # 转为统一格式并校验 tool
    calls = []
    for c in calls_raw:
        if not isinstance(c, dict):
            return None
        nc = _normalize_llm_call(c)
        if nc["tool_key"] not in TOOL_WHITELIST:
            return None
        calls.append(nc)

    # 转为 validator 格式（tool）并校验
    plan_for_val = {
        "calls": [{"tool": c["tool_key"], "params": c["params"]} for c in calls],
        "assumptions": llm_plan.get("assumptions") or {},
    }
    validated, errors = validate_plan(plan_for_val, question)
    if errors:
        return None

    # 转统一格式：validated 用 "tool"，我们需 tool_key+tool
    final_calls = []
    for c in validated.get("calls", []):
        tk = c.get("tool") or c.get("tool_key") or ""
        params = dict(c.get("params") or {})
        final_calls.append({"tool_key": tk, "tool": tk, "params": params})

    # 安全覆盖：diagnose 两日对比必须用指定结构
    intent = (slots.get("intent") or "unknown").strip()
    dt = slots.get("dt")
    prev_dt = slots.get("prev_dt")

    if intent == "diagnose_generic" and prev_dt and dt and prev_dt != dt:
        expected = [
            {"tool_key": "overview_day", "tool": "overview_day", "params": {"dt": prev_dt}},
            {"tool_key": "overview_day", "tool": "overview_day", "params": {"dt": dt}},
            {"tool_key": "funnel_daily", "tool": "funnel_daily", "params": {"days": 2, "end_dt": dt}},
        ]
        got = [(c["tool_key"], tuple(sorted((c.get("params") or {}).items()))) for c in final_calls[:3]]
        need = [(e["tool_key"], tuple(sorted(e["params"].items()))) for e in expected]
        if got != need:
            extra = [c for c in final_calls if c["tool_key"] == "category_contrib_buyers"][:1]
            if extra:
                extra[0]["params"]["dt"] = dt
            final_calls = expected + extra

    # days 限制 1-90
    for c in final_calls:
        p = c.get("params") or {}
        if "days" in p and p["days"] is not None:
            try:
                d = int(p["days"])
                c["params"]["days"] = max(1, min(90, d))
            except (ValueError, TypeError):
                pass

    assumptions = list(slots.get("assumptions") or [])
    if isinstance(validated.get("assumptions"), dict):
        for k, v in validated["assumptions"].items():
            if v is not None and f"{k}={v}" not in str(assumptions):
                assumptions.append(f"{k} 默认 {v}")
    elif isinstance(validated.get("assumptions"), list):
        assumptions.extend(validated["assumptions"])

    plots = _add_plots_from_calls(final_calls, question)
    return {
        "calls": final_calls,
        "plots": plots,
        "assumptions": assumptions,
        "not_supported": None,
    }


def _add_plots_from_calls(calls_list: list, q: str) -> list:
    """
    根据 calls 和问题动态生成 plots。
    - overview_daily / funnel_daily → trend 图
    - category_contrib_buyers → topn_bar 图
    - user_retention / user_activity → trend 图
    - overview_day → 默认不画图，除非问题含「画图/趋势图」
    """
    plots = []
    q_lower = (q or "").strip().lower()
    want_plot = "画图" in q_lower or "趋势图" in q_lower
    for i, c in enumerate(calls_list):
        tk = c.get("tool_key") or c.get("tool")
        if tk == "overview_day":
            if not want_plot:
                continue
            plots.append({
                "plot_type": "trend",
                "from_call": str(i),
                "config": {"x": "dt", "ys": ["pv", "uv", "buyers"], "title": "单日指标"},
            })
        elif tk == "overview_daily":
            plots.append({
                "plot_type": "trend",
                "from_call": str(i),
                "config": {"x": "dt", "ys": ["pv", "uv", "buyers"], "title": "Overview trend"},
            })
        elif tk == "funnel_daily":
            plots.append({
                "plot_type": "trend",
                "from_call": str(i),
                "config": {"x": "dt", "ys": ["uv_to_buyer", "uv_to_cart", "cart_to_buyer"], "title": "Funnel trend"},
            })
        elif tk == "category_contrib_buyers":
            plots.append({
                "plot_type": "topn_bar",
                "from_call": str(i),
                "config": {"x": "category_id", "y": "delta", "n": 10, "title": "Category contribution TopN"},
            })
        elif tk == "user_retention":
            plots.append({
                "plot_type": "trend",
                "from_call": str(i),
                "config": {"x": "dt", "ys": ["retention_1d"], "title": "次日留存率趋势"},
            })
        elif tk == "user_activity":
            plots.append({
                "plot_type": "trend",
                "from_call": str(i),
                "config": {"x": "dt", "ys": ["dau"], "title": "DAU 趋势"},
            })
    return plots


def _plan_from_slots_rule(question: str, slots: dict) -> dict[str, Any]:
    """规则型 plan 生成（原 plan_from_slots 逻辑），LLM 失败时回退。"""
    intent = (slots.get("intent") or "unknown").strip()
    dt = slots.get("dt")
    days = slots.get("days")
    assumptions = list(slots.get("assumptions") or [])
    not_supported = slots.get("not_supported")

    if not_supported is not None and isinstance(not_supported, dict):
        return {
            "calls": [],
            "plots": [],
            "assumptions": assumptions,
            "not_supported": not_supported,
        }

    calls: list[dict[str, Any]] = []

    def _add_call(tool_key: str, params: dict) -> None:
        calls.append({"tool_key": tool_key, "tool": tool_key, "params": params})

    # dt 优先约束：dt 非空且问题无范围词时
    if dt and not _has_range_words(question):
        assumptions.append("使用 dt 优先")
        if intent == "funnel_daily":
            # 单日转化率：funnel_daily(days=1, end_dt=dt)，返回 uv_to_buyer/uv_to_cart/cart_to_buyer
            _add_call("funnel_daily", {"days": 1, "end_dt": dt})
            return {"calls": calls, "plots": _add_plots_from_calls(calls, question), "assumptions": assumptions, "not_supported": None}
        if intent in TOOLS_NEED_DAYS:
            # 其余（overview_daily/retention/activity）：强制单日用 overview_day
            _add_call("overview_day", {"dt": dt})
            return {"calls": calls, "plots": _add_plots_from_calls(calls, question), "assumptions": assumptions, "not_supported": None}

    # diagnose_generic：最小证据链（最多 4 步）
    # 有 prev_dt 时：overview_day(prev_dt) + overview_day(dt) + funnel_daily(days=2, end_dt=dt)，精确定位两日对比
    # 无 prev_dt 时：overview_day(dt) + funnel_daily(days) 原有逻辑
    if intent == "diagnose_generic":
        target_dt = dt or _get_default_dt_safe()
        prev_dt = slots.get("prev_dt")
        if not dt:
            assumptions.append("dt 缺失，使用数据最新日")
        elif "使用 dt 优先" not in assumptions:
            assumptions.append("使用 dt 优先")
        q = (question or "").strip()

        if prev_dt and prev_dt != target_dt:
            assumptions.append(f"两日对比分析：{prev_dt} vs {target_dt}")
            _add_call("overview_day", {"dt": prev_dt})
            _add_call("overview_day", {"dt": target_dt})
            _add_call("funnel_daily", {"days": 2, "end_dt": target_dt})
        else:
            d = _clamp_days(days) if days is not None else DAYS_DEFAULT_OVERVIEW
            if days is None:
                assumptions.append(f"days 缺失，默认 {DAYS_DEFAULT_OVERVIEW}")
            elif days != d:
                assumptions.append("days 已限制到 1-90")
            _add_call("overview_day", {"dt": target_dt})
            _add_call("funnel_daily", {"days": d})
        if target_dt and any(k in q for k in ["类目", "哪个类目"]) and len(calls) < 4:
            _add_call("category_contrib_buyers", {"dt": target_dt})
        return {"calls": calls, "plots": _add_plots_from_calls(calls, question), "assumptions": assumptions, "not_supported": None}

    # unknown：无 calls
    if intent == "unknown":
        return {"calls": [], "plots": [], "assumptions": assumptions, "not_supported": None}

    # dt 类工具（dt 非空且无范围词时，"使用 dt 优先"已在前面加入 assumptions）
    if intent in TOOLS_NEED_DT:
        target_dt = dt or _get_default_dt_safe()
        if not dt:
            assumptions.append("dt 缺失，使用数据最新日")
        _add_call(intent, {"dt": target_dt})
        return {"calls": calls, "plots": _add_plots_from_calls(calls, question), "assumptions": assumptions, "not_supported": None}

    # days 类工具
    if intent in TOOLS_NEED_DAYS:
        if days is None:
            days = DAYS_DEFAULT_RETENTION if intent in ("user_retention", "user_activity") else DAYS_DEFAULT_OVERVIEW
            assumptions.append(f"days 缺失，默认 {days}")
        d = _clamp_days(days)
        if d != days:
            assumptions.append("days 已限制到 1-90")
        _add_call(intent, {"days": d})
        return {"calls": calls, "plots": _add_plots_from_calls(calls, question), "assumptions": assumptions, "not_supported": None}

    return {"calls": [], "plots": [], "assumptions": assumptions, "not_supported": None}


def plan_from_slots(question: str, slots: dict) -> dict[str, Any]:
    """
    从 mapper 输出 slots 生成 plan。
    优先调用 LLM 生成，再经规则层校验与 sanitize；失败时回退规则生成。
    输出：{calls: [{tool_key, params}], plots, assumptions, not_supported}
    """
    if USE_LLM_PLANNER:
        llm_plan = _call_llm_for_plan(question, slots)
        if llm_plan is not None:
            sanitized = _validate_and_sanitize_llm_plan(llm_plan, question, slots)
            if sanitized is not None:
                return sanitized
    return _plan_from_slots_rule(question, slots)


def _parse_days(q: str) -> int:
    """从问题解析天数。"""
    m = re.search(r"最近\s*(\d+)\s*天", q)
    if m:
        return min(int(m.group(1)), 90)
    if any(k in q for k in ["一周", "1周", "7天"]):
        return 7
    if any(k in q for k in ["两周", "14天"]):
        return 14
    return 9


def _parse_dt(q: str) -> str | None:
    """从问题解析日期，返回 YYYY-MM-DD。"""
    dates = _extract_dates_from_text(q)
    return dates[0] if dates else None


def plan_rule_based(question: str) -> dict[str, Any]:
    """
    规则型 Planner：根据关键词生成 Plan。
    可替换为 LLM 调用。
    """
    q = question.strip()
    if not q:
        return {"goal": "空问题", "calls": [], "assumptions": {}}

    # 不支持指标
    if any(k in q for k in ["GMV", "成交额", "销售额", "订单数", "客单价", "ARPU"]):
        return {
            "goal": q,
            "calls": [],
            "not_supported": {
                "reason": "数据集无价格/金额/订单字段",
                "suggestion": "可查 pv、uv、buyers、转化率等",
            },
        }

    dt = _parse_dt(q)
    days = _parse_days(q)

    # 类目贡献
    if "类目" in q or "类目贡献" in q:
        return {
            "goal": q,
            "calls": [{"tool": "category_contrib_buyers", "params": {"dt": dt or "2017-12-03"}, "why": "类目买家贡献"}],
            "assumptions": {"dt": dt} if dt else {},
        }

    # 新老用户转化
    if "新老" in q or "新用户" in q or "老用户" in q:
        return {
            "goal": q,
            "calls": [{"tool": "new_vs_old_user_conversion", "params": {"dt": dt or "2017-12-03"}, "why": "新老转化"}],
            "assumptions": {"dt": dt} if dt else {},
        }

    # 留存
    if "留存" in q:
        return {
            "goal": q,
            "calls": [{"tool": "user_retention", "params": {"days": days}, "why": "次日留存"}],
            "assumptions": {"days": days},
        }

    # 日活
    if "日活" in q or "DAU" in q:
        return {
            "goal": q,
            "calls": [{"tool": "user_activity", "params": {"days": days}, "why": "日活"}],
            "assumptions": {"days": days},
        }

    # 漏斗 / 转化
    if "漏斗" in q or ("转化" in q and "新老" not in q):
        return {
            "goal": q,
            "calls": [{"tool": "funnel_daily", "params": {"days": days}, "why": "漏斗转化"}],
            "assumptions": {"days": days},
        }

    # 单日 vs 多日
    if dt or "昨天" in q or "今日" in q or "当天" in q:
        from datetime import datetime, timedelta
        try:
            from tools.db import get_default_dt
            default_dt = get_default_dt()
        except Exception:
            default_dt = "2017-12-03"
        if "昨天" in q:
            d = datetime.strptime(default_dt[:10], "%Y-%m-%d") - timedelta(days=1)
            target_dt = d.strftime("%Y-%m-%d")
        else:
            target_dt = dt or default_dt
        return {
            "goal": q,
            "calls": [{"tool": "overview_day", "params": {"dt": target_dt}, "why": "单日核心指标"}],
            "assumptions": {"dt": target_dt},
        }

    # 默认：多日趋势
    return {
        "goal": q,
        "calls": [{"tool": "overview_daily", "params": {"days": days}, "why": "最近N天核心指标"}],
        "assumptions": {"days": days},
    }
