# 轻量粗规划架构

## Plan 结构（无硬 schema）

```json
{
  "goal": "用户要什么",
  "calls": [
    {"key": "overview_daily", "params": {"days": 9}},
    {"key": "diagnose_conversion_drop", "params": {"dt": "2017-12-03"}}
  ],
  "expected_evidence": ["dt", "pv", "uv", "buyers"],
  "risk_assumption": "数据缺什么要说明，或写 无"
}
```

- **goal**：自然语言描述
- **calls**：模板 key 或报告名 + params
- **expected_evidence**：从结果抽哪些字段（供 Narrator 用）
- **risk_assumption**：缺失/假设说明

## 流水线

```
用户输入 → LLM rough_planner_chat → arbitrate → execute → 输出
```

## 入口

```bash
# 规则回退（无 LLM）
python main_rough.py

# LLM 粗规划
python main_rough.py --llm
```

## 文件

| 文件 | 作用 |
|------|------|
| plan_schema.py | RoughPlan、from_dict、VALID_* |
| planner_prompt_rough.py | 轻量 prompt |
| llm.rough_planner_chat | 输出粗规划 JSON |
| arbiter_rough.py | 能力边界 + 参数补全 |
| executor_rough.py | 按 calls 执行 |
| pipeline_rough.py | 流水线 |
| main_rough.py | 入口 |
