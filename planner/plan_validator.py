# plan_validator.py
# Plan 校验器：工具白名单、日期覆盖、默认时间补全

from __future__ import annotations

import copy
import re
from typing import Any

# 工具白名单（与 tools.TOOL_REGISTRY 对齐）
TOOL_WHITELIST = frozenset({
    "overview_daily", "overview_day", "funnel_daily",
    "user_retention", "user_activity",
    "category_contrib_buyers", "new_vs_old_user_conversion",
})

# 需要 days 的工具
TOOLS_NEED_DAYS = frozenset({"overview_daily", "funnel_daily", "user_retention", "user_activity"})

# 需要 dt 的工具
TOOLS_NEED_DT = frozenset({"overview_day", "category_contrib_buyers", "new_vs_old_user_conversion"})

DEFAULT_DAYS = 9

# 明确日期正则
DATE_PATTERNS = [
    re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})"),                    # 2017-12-03, 2017/12/3
    re.compile(r"\b(\d{1,2})[-/](\d{1,2})\b"),                             # 12-03, 12/3（可能误杀）
    re.compile(r"(\d{1,2})月(\d{1,2})[号日]"),                             # 12月3日, 12月3号
    re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日?"),                       # 2017年12月3日
]


def _extract_dates_from_text(text: str) -> list[str]:
    """从用户文本提取明确日期，返回 YYYY-MM-DD 列表。"""
    if not text or not isinstance(text, str):
        return []
    found: list[str] = []
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            g = m.groups()
            try:
                if len(g) == 3:  # 2017-12-03 或 2017年12月3日
                    y, mo, d = int(g[0]), int(g[1]), int(g[2])
                elif len(g) == 2:
                    if g[0].isdigit() and len(g[0]) == 4:  # 年
                        y, mo, d = int(g[0]), int(g[1]), 1
                    else:
                        mo, d = int(g[0]), int(g[1])
                        y = 2017  # 无年时默认
                else:
                    continue
                if 1 <= mo <= 12 and 1 <= d <= 31:
                    found.append(f"{y}-{mo:02d}-{d:02d}")
            except (ValueError, IndexError):
                continue
    return list(dict.fromkeys(found))  # 去重保序


def _plan_has_time_params(plan: dict[str, Any]) -> bool:
    """Plan 中任意 call 是否已有时间参数（dt/days/start/end）。"""
    calls = plan.get("calls") or []
    for c in calls:
        p = c.get("params") or {}
        if any(k in p and p.get(k) for k in ("dt", "days", "start", "end")):
            return True
    return False


def _plan_covers_date(plan: dict[str, Any], date_str: str) -> bool:
    """Plan 中是否有 call 的 dt 或 start/end 覆盖该日期。"""
    calls = plan.get("calls") or []
    for c in calls:
        p = c.get("params") or {}
        dt = p.get("dt")
        if dt and str(dt)[:10] == date_str[:10]:
            return True
        start, end = p.get("start"), p.get("end")
        if start and end:
            if start <= date_str[:10] <= end:
                return True
    return False


def _inject_default_days(plan: dict[str, Any]) -> None:
    """无时间参数时，给需 days 的 call 补 days=9，并写入 assumptions。"""
    calls = plan.get("calls") or []
    if not calls:
        return
    if _plan_has_time_params(plan):
        return
    injected = False
    for c in calls:
        t = c.get("tool")
        if t in TOOLS_NEED_DAYS:
            p = c.setdefault("params", {})
            if "days" not in p or p.get("days") is None:
                p["days"] = DEFAULT_DAYS
                injected = True
    if injected:
        assumptions = plan.setdefault("assumptions", {})
        if isinstance(assumptions, dict) and "days" not in assumptions:
            assumptions["days"] = DEFAULT_DAYS


def validate_plan(plan: dict[str, Any], user_text: str = "") -> tuple[dict[str, Any], list[str]]:
    """
    校验并自动修补 Plan。
    返回 (修正后的 plan, 错误列表)。错误为空则通过。
    """
    plan = copy.deepcopy(plan)
    errors: list[str] = []

    calls = plan.get("calls") or []
    if not isinstance(calls, list):
        errors.append("calls 必须是数组")
        return plan, errors

    # 1. tool 必须在白名单
    for i, c in enumerate(calls):
        if not isinstance(c, dict):
            errors.append(f"calls[{i}] 必须是对象")
            continue
        tool = c.get("tool")
        if not tool:
            errors.append(f"calls[{i}] 缺少 tool")
        elif tool not in TOOL_WHITELIST:
            errors.append(f"calls[{i}] tool '{tool}' 不在白名单，可用: {sorted(TOOL_WHITELIST)}")

    # 2. 用户文本有明确日期时，Plan 必须包含 dt 或 start/end 覆盖（not_supported 时跳过）
    if not plan.get("not_supported") and calls:
        dates = _extract_dates_from_text(user_text)
        for d in dates:
            if not _plan_covers_date(plan, d):
                errors.append(f"用户提到日期 {d}，但 Plan 中无 dt 或 start/end 覆盖该日")

    # 3. 无时间参数时自动补默认最近 9 天
    _inject_default_days(plan)

    return plan, errors
