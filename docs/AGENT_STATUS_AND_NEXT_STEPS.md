# Agent 实现状态与下一步

## 一、已实现的步骤与流程

### 1. 整体流程

```
用户输入问题
    │
    ▼
main.py 主循环 (input → route_question → 分支处理 → 输出)
    │
    ├─► starter.init()           # 初始化 DB 连接，注入 core.engine
    ├─► get_queryable_date_range # 显示可查询时间范围
    │
    ▼
core.route_question(q)           # 规则路由 → (intent, params)
    │
    ├─► check_not_supported     # 优先：GMV/订单数/ARPU → not_supported
    ├─► extract_date / extract_days / get_default_dt  # 时间参数解析
    ├─► 关键词匹配 → 20+ 条规则，按优先级返回 intent
    └─► 无数据关键词 → unrecognized
    │
    ▼
main 分支分发
    │
    ├─► not_supported          # 输出 reason + missing_desc
    ├─► unrecognized           # 输出「暂不支持此类问题」
    ├─► diagnose_*             # 诊断报告（diagnose_uv_drop / diagnose_buyers_drop / diagnose_conversion_drop）
    ├─► *_anomaly              # 异常报告（uv / buyers / conversion）
    ├─► *_forecast             # 预测报告（conversion_rate / uv）
    ├─► cart_abandonment       # 购物车流失
    ├─► page_load_time_conversion / region_conversion_diff  # TYPE_E 兜底
    └─► 模板分支               # run_template → render_answer（带 try/except）
```

### 2. 核心组件

| 组件 | 文件 | 作用 |
|------|------|------|
| **Plan Schema** | planner_schema.py | PlannerOutput、TimeRange、ToolCall、NotSupported、JSON Schema |
| **指标字典** | planner_schema.py | METRIC_DICT（14 个）、NOT_SUPPORTED（gmv/order_count/arpu） |
| **路由层** | core.route_question | 规则路由，20+ intent，含 check_not_supported、unrecognized |
| **执行层** | core.run_template / run_sql | 模板优先 + 护栏（dt 过滤、LIMIT、表白名单） |
| **Narrator** | narrator.py | 固定骨架：结论 / 证据 / 诊断路径 / 下一步建议 |
| **渲染器** | core.render_* | 各 intent 对应 render，空结果有兜底 |
| **诊断引擎** | core.diagnose_* / report_* | UV/买家/转化诊断、异常、预测、购物车流失 |
| **数据就绪** | intent_data_status.py | DATA_OK / BOUNDARY / TYPE_E 映射 |

### 3. 已支持的 Intent（按 main 分支顺序）

| 类别 | Intent | 路由关键词示例 |
|------|--------|----------------|
| 边界 | not_supported | GMV、成交额、订单数、客单价 |
| 边界 | unrecognized | 无数据关键词 |
| 诊断 | diagnose_uv_drop | 为什么 UV 下降 |
| 诊断 | diagnose_buyers_drop | 为什么买家数下降 |
| 诊断 | diagnose_conversion_drop | 为什么转化下降 |
| 异常 | uv_anomaly / buyers_anomaly / conversion_anomaly | 异常、波动 |
| 预测 | conversion_rate_forecast / uv_forecast | 预测、未来 |
| 其他 | cart_abandonment | 购物车流失 |
| 其他 | page_load_time_conversion | 加载时间 + 转化 |
| 其他 | region_conversion_diff | 地区转化 |
| 模板 | overview_daily | 最近 N 天趋势 |
| 模板 | overview_day | 当日核心指标 |
| 模板 | funnel_daily / daily_funnel | 漏斗 |
| 模板 | user_retention / user_activity | 留存、活跃 |
| 模板 | new_vs_old_user_conversion / user_level_conversion | 新老用户、分层 |
| 模板 | category_contrib / category_growth / category_contrib_buyers | 类目贡献 |
| 兜底 | daily_overview | 未匹配时的默认 |

### 4. 已注册的 SQL 模板（register_*.py）

| 脚本 | 模板 key | 说明 |
|------|----------|------|
| register_overview_daily.py | overview_daily | 最近 N 天 pv/uv/buyers |
| register_overview_day.py | overview_day | 当日核心指标 |
| register_funnel_daily.py | funnel_daily | 漏斗各环节 |
| register_user_retention.py | user_retention | 留存 |
| register_user_activity.py | user_activity | 活跃 |
| register_new_vs_old_user_conversion.py | new_vs_old_user_conversion | 新老用户转化 |
| register_user_level_conversion.py | user_level_conversion | 分层转化 |

**缺失的 register**：`daily_funnel`、`category_contrib_buyers`（诊断引擎依赖）

### 5. 边界与兜底

- **NOT_SUPPORTED**：route_question 开头检查，main 有分支
- **unrecognized**：无数据关键词 → 专门分支
- **空结果**：各 render 有 `if df.empty` 分支
- **SQL 失败**：main 模板分支 try/except，输出「模板未注册」或「DB 异常」
- **时间边界**：extract_days 上限 90 天、extract_date 用数据年份推断

---

## 二、下一步应该做什么

### 优先级 1：补齐核心用例的模板依赖

当前 **「为什么 12-03 转化下降？」** 依赖 `daily_funnel` 和 `category_contrib_buyers`，但无对应 register 脚本。

1. **新增 register_daily_funnel.py**  
   - 注册 `daily_funnel` 模板  
   - SQL 输出：dt, pv, uv, buyers, cart_users, uv_to_buyer, uv_to_cart, cart_to_buyer  
   - 依赖 `ub.daily_metrics`（或等价表）

2. **新增 register_category_contrib_buyers.py**（若数据有 category_id）  
   - 注册 `category_contrib_buyers` 模板  
   - SQL 输出：category_id, buyers_cur, buyers_prev, delta  
   - 依赖 `ub.user_behavior` 或按类目聚合的表  
   - 若无 category_id：在 diagnose_conversion_drop 中优雅降级，跳过类目贡献

### 优先级 2：端到端验证 3 条用例

| 用例 | 当前状态 | 建议动作 |
|------|----------|----------|
| 最近 9 天核心指标趋势 | 依赖 overview_daily | 运行 `python register_overview_daily.py` 后实测 |
| 为什么 12-03 转化下降 | 依赖 daily_funnel | 完成 register_daily_funnel 后实测 |
| GMV 多少 | 已实现 not_supported | 直接实测验证输出 |

### 优先级 3：慢查询兜底（可选）

- 在 `run_template` / `run_sql` 中增加超时（如 `statement_timeout` 或 SQLAlchemy timeout）
- 超时时输出可解释提示，例如「查询超时，请缩小时间范围或添加筛选条件」

### 优先级 4：Planner LLM 接入（可选，M1 升级）

- 当前为规则路由，未使用 planner_prompt + LLM  
- 若接入 LLM：调用 `get_planner_json_schema()` 做 structured output，用 LLM 产出 Plan，再根据 Plan 的 tool_calls 执行

### 优先级 5：报告分支的 SQL 失败兜底

- 诊断/异常/预测等 report 分支内部会调用 `run_template`  
- 若模板缺失，会抛出未捕获异常  
- 可在 main 中对这些分支统一加 try/except，或在各 report 函数内部做捕获与友好输出

---

## 三、快速检查清单

- [ ] 运行 `python register_overview_daily.py` 确保 overview_daily 模板已注册
- [ ] 新建并运行 `register_daily_funnel.py`
- [ ] 若有 category_id，新建并运行 `register_category_contrib_buyers.py`
- [ ] 使用 `python main.py` 实测 3 条用例
- [ ] （可选）为 report 分支增加异常兜底
- [ ] （可选）增加 run_template/run_sql 超时
