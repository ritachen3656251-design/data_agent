# plan_schema.py
# 最小 Plan 结构：够用即可

from __future__ import annotations

from typing import Any, TypedDict

try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired


class Call(TypedDict):
    """每个 call 必须有 tool + params。"""
    tool: str
    params: dict[str, Any]
    why: NotRequired[str]


class PlotSpec(TypedDict):
    """每个 plot 项：plot_type, from_call, config"""
    plot_type: str  # trend | topn_bar
    from_call: str  # call 索引，如 "0"
    config: dict[str, Any]  # 传给 plot_tools 的参数


class Plan(TypedDict, total=False):
    goal: str
    calls: list[Call]
    plots: NotRequired[list[PlotSpec]]  # 可选，默认由 planner 按 tool_key 添加
    assumptions: dict[str, Any]
    not_supported: NotRequired[dict[str, Any]]  # 可选，仅无法回答时填


# 示例
# plan: Plan = {
#     "goal": "最近 7 天核心指标趋势",
#     "calls": [
#         {"tool": "overview_daily", "params": {"days": 7}, "why": "按天返回 pv/uv/buyers"}
#     ],
#     "assumptions": {"days": 7},
# }
#
# 无法回答时：
# plan: Plan = {
#     "goal": "GMV 是多少",
#     "calls": [],
#     "not_supported": {"reason": "数据集无价格/金额字段", "suggestion": "可查 buyers/uv 等"},
# }
