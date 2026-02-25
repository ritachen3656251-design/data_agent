# Planner 层问题诊断

## 一、为什么「总是出现错误」？

### 1. LLM 输出 JSON 解析失败
- **现象**：LLM 可能输出 markdown 代码块（\`\`\`json ... \`\`\`）、前后说明文字、或格式略偏
- **现状**：`llm.planner_chat` 解析失败时直接 fallback 到 `{"intent": "overview", "error": "..."}`
- **结果**：无论用户问什么，最终都变成 overview

### 2. LLM 的 tool_calls 被完全忽略
- **现象**：Planner 的 schema 和 prompt 要求输出 tool_calls，LLM 也会输出
- **现状**：`executor.plan_to_exec_spec` **从不读取** `plan.raw["tool_calls"]`，只用硬编码的 `INTENT_TO_EXEC` 映射
- **结果**：LLM 选定的 template/report 被丢弃，统一按 intent 映射成固定动作

### 3. 类型不匹配（days 等）
- **现象**：LLM 可能输出 `"days": "9"`（字符串）而非 `9`（整数）
- **现状**：`_dict_to_plan` 未做类型转换
- **结果**：后续判断 `plan.days <= 0` 等可能报错或行为异常

---

## 二、为什么「被规则写死」？

### 1. extracted 把「默认值」当成「用户显式」
- **现象**：用户说「看一下数据」（未提时间），`parse_time` 返回 `type=days, days=9`（默认）
- **现状**：pipeline 会设 `explicit_days=True`
- **结果**：Arbiter 用「显式 9 天」覆盖 LLM 的 time_range，哪怕 LLM 给出的是 dt

### 2. 规则提取覆盖 LLM 的意图
- **现象**：`extracted` 只来自 `parse_time`，没有区分「用户明确说的」和「默认填的」
- **结果**：只要命中规则（如「最近9天」），LLM 的 time 判断就会被完全覆盖

### 3. Executor 的 INTENT_TO_EXEC 写死映射
- **现象**：`diagnose` → `diagnose_conversion_drop`，`anomaly` → `uv_anomaly` 等全部写死
- **结果**：即使用 LLM 正确识别「为什么 UV 下降」，也可能被固定成 `diagnose_conversion_drop`

---

## 三、修复方向（已实施）

1. **Executor 优先使用 tool_calls**：若 `plan.raw` 中有 `tool_calls`，按 LLM 选择执行
2. **区分「显式」与「默认」**：`parse_time` 增加 `explicit` 标记，仅用户明确表达时设为 True
3. **鲁棒 JSON 解析**：支持多种格式、容错、类型转换
4. **类型规范化**：`days` 等统一转为 int
