# 三层架构说明

## Layer A：LLM Planner（意图与槽位）

**输出**：Plan = 意图 + time + metrics + dimension + operation

- **不决定最终 SQL**，只表达「要什么」
- `plan_schema.Plan`：intent, time_mode, dt/days/start/end, metrics, dimension, operation

## Layer B：Rule Validator / Arbiter（仲裁器）

**职责**：把 LLM 的不确定输出修正到可执行、且不违反内核规则的状态

### 1. 优先级规则（显式 > 默认）

- 用户给了 `dt`，就不能用「最近7天」覆盖
- 用户给了 `days`，保留用户值

### 2. 一致性约束

- `mode=day` 不能带 range 参数（days, start, end 清空）
- `mode=days` 不能带 dt / start / end
- `mode=range` 必须同时有 start 和 end

### 3. 能力边界

- 问 GMV → `not_supported` + 缺字段说明
- metrics 非法 → fallback 到核心指标 [uv, buyers]

## Layer C：Deterministic Executor（模板/查询构建器）

**职责**：模板只负责「按参数计算」，不决定「回答是什么」

- 输入：仲裁后的 Plan
- 输出：`(df, intent, context)` 或 `(None, "not_supported", context)`
- 模板 / 报告：按 `plan_to_exec_spec` 映射到 `(template_key|report_name, params)` 后执行

## 流水线

```
用户输入
   │
   ▼
┌─────────────────┐
│ Layer A: Planner │  ← LLM 或规则回退
│  raw Plan (dict) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Layer B: Arbiter │  ← 优先级 / 一致性 / 能力边界
│ validated Plan   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Layer C: Executor│  ←  deterministic (template_key, params)
│  df / report     │
└────────┬────────┘
         │
         ▼
    Narrator 渲染
```

## 入口

```bash
# 规则 Arbiter → Executor（无 LLM）
python main_pipeline.py

# LLM Planner → Arbiter → Executor
python main_pipeline.py --llm
```
