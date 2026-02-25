# 评估指标体系

运行：`python run_eval_metrics.py`（基础指标）或 `python run_eval_metrics.py --full`（含幻觉率，需 DB）

---

## 指标定义

| 指标 | 公式 | 分母 | 说明 |
|------|------|------|------|
| **意图识别准确率** | 正确 intent 数 / 30 | 30 | E01–E20 + B01–B10，mapper 输出的 intent 与预期一致 |
| **参数抽取准确率** | 正确 dt 数 / 需要 dt 的题目数 | 需 dt 的题数 | 仅统计 overview_day、category_contrib_buyers、new_vs_old_user_conversion、diagnose（有 dt）的题 |
| **模板命中率** | 正确 template 数 / 30 | 30 | plan.calls 的 tool_key 与预期一致（含 calls_contain 子集匹配） |
| **边界处理正确率** | 正确兜底题目数 / 5 | 5 | B05、B06、B03、B07、B10：极短/超限/无日期等边界 |
| **幻觉率** | 出现不存在字段次数 / 30 | 30 | 输出文本中含 answer_obj 中不存在的数字的次数（需 `--full`） |

---

## 题目集划分

- **30 题（意图/模板）**：E01–E20、B01–B10
- **5 题（边界兜底）**：B05 数据、B06 看看、B03 最近100天、B07 类目贡献、B10 新老转化
- **失败类 F01–F10**：不参与上述指标，用于回归 not_supported 等

---

## 边界题目说明

| ID | 问题 | 预期行为 |
|----|------|----------|
| B03 | 最近100天数据 | days 超限，clamp 至 90 |
| B05 | 数据 | 极短无日期，默认 overview_daily(days=9) |
| B06 | 看看 | 极短模糊，默认 overview_daily |
| B07 | 类目贡献 | 无日期，用默认 dt |
| B10 | 新老转化 | 无日期，用默认 dt |
