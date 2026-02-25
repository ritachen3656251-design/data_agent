# 标准评估集：20 条测试问题

结构：**5 描述 + 5 诊断 + 5 归因 + 5 异常**

数据文件：`eval_standard_20.jsonl`（每行一条 JSON）

---

## 1. 描述类（5 条）

| ID | 问题 | 正确模板 | 正确参数 | 预期输出结构 |
|----|------|----------|----------|--------------|
| E01 | 2017-12-03 核心指标如何？ | overview_day | dt=2017-12-03 | answer_obj: headline, evidence≥2(uv/buyers/pv), analysis_notes, next_actions |
| E02 | 最近9天核心指标趋势 | overview_daily | days=9 | answer_obj: headline, evidence≥2, insights(extreme/recent_change/top_swing_day), next_actions；plot: trend(pv,uv,buyers) |
| E03 | 最近7天漏斗转化表现 | funnel_daily | days=7 | answer_obj: headline, evidence≥2, insights(biggest_funnel_change), next_actions；plot: trend(uv_to_buyer,uv_to_cart,cart_to_buyer) |
| E04 | 最近7天留存率 | user_retention | days=7 | answer_obj: headline, evidence≥1, insights, next_actions；plot: trend(retention_1d) |
| E05 | 12月3日新老用户转化差异 | new_vs_old_user_conversion | dt=2017-12-03 | answer_obj: headline, evidence≥2(new_cvr/old_cvr等), next_actions |

---

## 2. 诊断类（5 条）

| ID | 问题 | 正确模板 | 正确参数 | 预期输出结构 |
|----|------|----------|----------|--------------|
| E06 | 为什么12月3日转化下降？ | diagnose_generic | overview_day(dt=2017-12-03), funnel_daily(days=9) | plan: calls≥2 含 overview_day+funnel_daily；answer_obj: is_diagnose, evidence≥3, 结论/证据链/假设/下一步 |
| E07 | 12月3日买家下降原因分析 | diagnose_generic | overview_day(dt=2017-12-03), funnel_daily(days=9) | 同上 |
| E08 | 最近转化率下降怎么回事 | diagnose_generic | overview_day(dt), funnel_daily(days=9) | 同上，dt 可缺省用最新日 |
| E09 | 昨天UV掉了是什么原因 | diagnose_generic | overview_day(dt=2017-12-03), funnel_daily(days=9) | 同上 |
| E10 | 12月2日为什么数据变差 | diagnose_generic | overview_day(dt=2017-12-02), funnel_daily(days=9) | 同上 |

---

## 3. 归因类（5 条）

| ID | 问题 | 正确模板 | 正确参数 | 预期输出结构 |
|----|------|----------|----------|--------------|
| E11 | 12月3日哪些类目导致买家下降？ | category_contrib_buyers | dt=2017-12-03 | answer_obj: headline(拖累/拉动), evidence, insights(top_delta,concentration), next_actions；plot: topn_bar(category_id,delta) |
| E12 | 12月3日哪些类目拉动增长 | category_contrib_buyers | dt=2017-12-03 | 同上 |
| E13 | 2017-12-02 类目贡献Top5 | category_contrib_buyers | dt=2017-12-02 | 同上 |
| E14 | 昨天类目拖累情况 | category_contrib_buyers | dt=2017-12-03 | 同上 |
| E15 | 12.3 主要拉动来自哪个类目 | category_contrib_buyers | dt=2017-12-03 | 同上 |

---

## 4. 异常类（5 条）

| ID | 问题 | 正确模板 | 正确参数 | 预期输出结构 |
|----|------|----------|----------|--------------|
| E16 | 最近数据波动怎么回事 | diagnose_generic | overview_day(dt), funnel_daily(days=9) | 同诊断类 |
| E17 | 12月3日有异常吗 | overview_day | dt=2017-12-03 | 同描述类 E01（单日数据查询） |
| E18 | 最近9天UV波动原因 | diagnose_generic | overview_day(dt), funnel_daily(days=9) | 同诊断类 |
| E19 | 12月2日数据跌了怎么回事 | diagnose_generic | overview_day(dt=2017-12-02), funnel_daily(days=9) | 同诊断类 |
| E20 | 最近转化率异常分析 | diagnose_generic | overview_day(dt), funnel_daily(days=9) | 同诊断类 |

---

## 5. 日期回归（D01-D13，11月25日-12月3日全覆盖）

| ID | 问题 | 正确 dt | 备注 |
|----|------|---------|------|
| D01-D08 | 11.25-12.03、12.1、12/1、12-01、12月1日、2017-12-01、11月25日、11月30日 | 见原表 | 日期解析格式 |
| D09-D13 | 11月26日、11月27日、11月28日、11月29日、12月2日 核心指标 | 2017-11-26 … 2017-12-02 | 11月25-12月3日各日覆盖 |

---

## 6. 复杂问题（CX01-CX02，诊断+归因混杂）

| ID | 问题 | 正确 intent | 断言 |
|----|------|-------------|------|
| CX01 | 为什么12月3日转化下降，是哪些类目导致的 | diagnose_generic | calls 含 overview_day、funnel_daily、category_contrib_buyers |
| CX02 | 12月2日买家掉了，哪个类目拖累的 | diagnose_generic | 同上 |

## 7. 暂时无法查询（F11-F14）

问及以下指标时，系统应返回「暂时无法查询」：

| ID | 问题 | 预期 |
|----|------|------|
| F11 | 新老用户转化率多少 | not_supported |
| F12 | 12月3日买家数 | not_supported |
| F13 | 最近7天日活 | not_supported |
| F14 | 次日留存率 | not_supported |

## 8. 显式指标（PM01-PM02）

问 PV/UV/买家数 时应返回具体指标，而非笼统「核心指标」：

| ID | 问题 | 预期 |
|----|------|------|
| PM01 | 12月2日PV/UV/买家数 | headline 含 PV，evidence 含 pv、uv、buyers |
| PM02 | 12月3日PV、UV、买家数 | 同上 |

## 9. 漏斗隔离（FN01-FN03）

问漏斗时**不得**返回核心指标（PV/UV/买家），plan 中**不得**含 overview_day：

| ID | 问题 | 正确 intent | 断言 |
|----|------|-------------|------|
| FN01 | 最近7天漏斗 | funnel_daily | tool_keys=[funnel_daily]，calls_must_not_contain=[overview_day] |
| FN02 | 漏斗转化怎么样 | funnel_daily | calls_contain=[funnel_daily]，calls_must_not_contain=[overview_day] |
| FN03 | 最近5天漏斗转化 | funnel_daily | 同上 |

---

## 通用预期结构说明

### slots（mapper 输出）
- `intent`: 上述表格中的 intent
- `dt`: YYYY-MM-DD 或 null
- `days`: 1–90 或 null
- `assumptions`: list[str]
- `not_supported`: null（本评估集均为可支持问题）

### plan（planner 输出）
- `calls`: [{tool_key, params}]
- `plots`: [{plot_type, from_call, config}]
- `assumptions`: list
- `not_supported`: null

### answer_obj（narrator 输出）
- `headline`: str
- `evidence`: list[{label, value, source}]
- `analysis_notes`: list[str]
- `assumptions`: list[str]
- `limitations`: list[str]
- `next_actions`: list[{suggestion, tool_key}]
- `insights`: list[{type, text, importance, ...}]（若适用）
- `tool_key`: str
