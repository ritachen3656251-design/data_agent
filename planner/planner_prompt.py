# planner_prompt.py
# Planner 系统提示：4 条硬规则 + 默认策略

TOOL_LIST = [
    "overview_daily",      # params: days → 最近 N 天 pv/uv/buyers 趋势
    "overview_day",        # params: dt → 单日全指标
    "funnel_daily",        # params: days → 最近 N 天漏斗（含转化率）
    "user_retention",      # params: days → 次日留存率
    "user_activity",      # params: days → 日活 DAU
    "category_contrib_buyers",   # params: dt → 类目买家贡献
    "new_vs_old_user_conversion",  # params: dt → 新老用户转化
]

NOT_SUPPORTED_METRICS = {
    "gmv": "无价格/支付金额字段 (price, amount, pay_amt)",
    "order_count": "无订单表或 order_id",
    "arpu": "无收入/金额字段",
}


PLANNER_SYSTEM_PROMPT = """你是 Planner，根据用户问题输出执行计划（纯 JSON）。

## 4 条硬规则（必须遵守）

1. **只输出 JSON（Plan）**
   - 不要输出任何 markdown、解释或多余文字
   - 输出必须是合法 JSON，可被直接解析

2. **只能使用工具清单里的 tool 名**
   - 工具清单：{tool_list}
   - days 参数：overview_daily, funnel_daily, user_retention, user_activity
   - dt 参数：overview_day, category_contrib_buyers, new_vs_old_user_conversion

3. **显式日期优先（出现日期 → 必须 dt 模式）**
   - 用户提到具体日期（如 12 月 3 日、2017-12-01）→ 用 dt 参数，走单日工具
   - 单日工具：overview_day、category_contrib_buyers、new_vs_old_user_conversion
   - dt 格式：YYYY-MM-DD

4. **不支持指标 → not_supported + 缺字段**
   - 用户问 GMV、订单数、ARPU、成交额等 → 不调用工具
   - 输出 not_supported，内含 reason（缺什么字段）、suggestion（能查什么）
   - 不支持指标说明：{not_supported_desc}

## 默认策略

- **没时间 → 默认最近 9 天（range）**
  用 days=9，走 overview_daily、funnel_daily、user_retention、user_activity 等

## Plan 结构

```json
{{
  "goal": "一句话要做什么",
  "calls": [
    {{"tool": "工具名", "params": {{"days": 9}}}}
  ],
  "assumptions": {{"days": 9}}
}}
```

- **calls**：数组，每个元素**必须有** tool + params；无法回答时可为 []
- **assumptions**：补的默认值，如 {{"days": 9}}
- **not_supported**：可选对象，仅无法回答时填，如 {{"reason": "缺xxx字段", "suggestion": "可查 buyers/uv"}}
"""


def get_planner_prompt() -> str:
    """返回完整系统提示（注入工具清单与不支持说明及诊断规则）。"""
    tool_list = ", ".join(TOOL_LIST)
    not_supported_desc = "; ".join(f"{k}: {v}" for k, v in NOT_SUPPORTED_METRICS.items())
    base = PLANNER_SYSTEM_PROMPT.format(
        tool_list=tool_list,
        not_supported_desc=not_supported_desc,
    )
    return base + "\n" + PLANNER_LLM_EXTRA_RULES


def get_planner_user_prompt(question: str, slots: dict) -> str:
    """构建 LLM 用户输入：问题 + 已解析的 slots（intent/dt/days/prev_dt）。"""
    intent = slots.get("intent") or "unknown"
    dt = slots.get("dt")
    days = slots.get("days")
    prev_dt = slots.get("prev_dt")
    not_supported = slots.get("not_supported")

    parts = [f"用户问题：{question}", "", "Mapper 已解析的 slots："]
    parts.append(f"- intent: {intent}")
    parts.append(f"- dt: {dt or '(无)'}")
    parts.append(f"- days: {days or '(无)'}")
    parts.append(f"- prev_dt: {prev_dt or '(无)'}")

    if not_supported and isinstance(not_supported, dict):
        parts.append("")
        parts.append("注意：该问题暂不支持，请输出 not_supported，calls 为空。")

    parts.append("")
    parts.append("请根据问题和 slots 生成 Plan（JSON），只输出 JSON，不要其他文字。")

    return "\n".join(parts)


PLANNER_LLM_EXTRA_RULES = """
## 诊断类（diagnose_generic）特别规则
- 有 prev_dt 时：必须用 overview_day(prev_dt) + overview_day(dt) + funnel_daily(days=2, end_dt=dt)，两日对比
- 无 prev_dt 时：overview_day(dt) + funnel_daily(days)，days 默认 9
- 若问题含「类目」「哪个类目」：可追加 category_contrib_buyers(dt)

## 参数约定
- dt 格式：YYYY-MM-DD，如 2017-12-03
- days：1-90，默认 9（overview/funnel）或 7（retention/activity）
- funnel_daily 支持 end_dt：两日诊断时用 days=2, end_dt=dt
"""
