# 实现检查报告：M0 / M1 / M2

## M0（1 天内能完成的最小可跑版本）

### ✓ 定义 Plan schema
- **planner_schema.py**：PlannerOutput、intent、metrics、time_range、group_by、filters、tool_calls、not_supported、evidence_metrics 等
- **get_planner_json_schema()**：JSON Schema 供 LLM structured output

### ✓ 指标字典 + NOT_SUPPORTED
- **METRIC_DICT**：aliases + desc，14 个指标
- **NOT_SUPPORTED**：gmv、order_count、arpu
- **map_user_term_to_metric()**、**check_not_supported()**

### ✓ PostgreSQL tool：run_sql_template + run_sql（带护栏）
- **run_sql_template**：有，从 ub.sql_templates 读取并执行（PostgreSQL）
- **run_sql**：有，护栏（dt 过滤、LIMIT、禁止 SELECT * FROM user_behavior、表白名单 ub.daily_metrics / ub.user_behavior）

### ✓ Narrator 固定骨架输出
- **narrator.py**：结论 / 证据 / 诊断路径 / 下一步建议
- **format_narrator()**、**format_narrator_minimal()**
- 各 render/report 已按骨架输出

### 3 条用例跑通情况

| 用例 | 路由 | 执行路径 | 状态 |
|------|------|----------|------|
| **「最近9天核心指标趋势？」** | overview_daily, {days: 9} | run_template("overview_daily", {days:9}) → render_overview_daily | ✓ 可跑（需 overview_daily 模板已注册） |
| **「为什么12-03转化下降？」** | diagnose_conversion_drop, {dt: "2017-12-03"} | report 分支 → diagnose_conversion_drop → daily_funnel + category_contrib_buyers | ✓ 可跑（需 daily_funnel、category_contrib_buyers 模板） |
| **「GMV多少？」** | not_supported | check_not_supported 命中 → 输出 reason + missing_desc | ✓ 已实现 |

---

## M1（让它像「智能体」）

### ✓ Planner 支持多意图
- overview / compare / attribution / anomaly / diagnose / forecast / other 已定义
- **INTENT_TO_PROJECT_INTENTS** 映射完备

### ✓ 澄清策略（默认值兜底）
- **extract_days(q)**：无明确天数时默认 7
- **get_default_dt()**：无日期时用数据最新日
- **extract_date(q)**：月日无年时用数据年份
- 澄清策略为「不问用户，用默认值」

### ✓ SQL 失败 / 空结果兜底
- **空结果**：各 render 已有「结论：没有返回…」等
- **SQL 失败**：main 模板分支 try/except，模板未注册或 DB 异常时输出可解释错误
- **慢查询**：未做超时与可解释提示（待补齐）

---

## M2（L2 诊断引擎体系化扩容）

### ✓ diagnose_uv_drop / diagnose_buyers_drop
- 已实现，输出结论 / 证据 / 诊断路径 / 下一步

### ✓ anomaly 系列
- uv_anomaly、buyers_anomaly、conversion_anomaly 已实现

### ✓ 诊断树标准化
- **diagnose_buyers_drop**：先判断流量(uv) vs 效率(buyers/uv)，再建议下钻类目贡献
- **diagnose_conversion_drop**：拆解 UV→Cart、Cart→Buyer，并拉类目贡献 Top20
- **diagnose_uv_drop**：环比对比

---

## 边界案例检查（含 GMV 及扩展类型）

### 类型 A：不支持的指标（NOT_SUPPORTED）

| 案例 | 示例问题 | check_not_supported | route_question | main 分支 | 状态 |
|------|----------|---------------------|----------------|-----------|------|
| **GMV** | GMV多少？/ 成交额多少？/ 销售额 | ✓ 能识别 | ✓ 路由前调用 | ✓ not_supported 分支 | ✓ 已实现 |
| **订单数** | 订单数多少？/ 订单量 | ✓ 能识别 | ✓ 调用 | ✓ 分支 | ✓ 已实现 |
| **ARPU** | 客单价多少？/ ARPU | ✓ 能识别 | ✓ 调用 | ✓ 分支 | ✓ 已实现 |

### 类型 B：空结果

| 案例 | 场景 | 处理方式 | 状态 |
|------|------|----------|------|
| 模板返回空 DataFrame | 日期超出数据范围、无匹配记录 | 各 render 有 `if df.empty` 分支，输出「结论：没有返回…」「下一步建议：检查日期或数据范围」 | ✓ 已覆盖 |
| daily_funnel 缺 dt/dt-1 | 无法做环比 | 输出「找不到 X 或 Y 的漏斗数据」 | ✓ 已覆盖 |
| category_growth 无正增长 | 所有类目 delta≤0 | 有专门分支，输出「无正增长类目」+ 下一步建议 | ✓ 已覆盖 |

### 类型 C：SQL/模板执行失败

| 案例 | 场景 | 处理方式 | 状态 |
|------|------|----------|------|
| 模板不存在 | ub.sql_templates 无该 template_key | run_template 抛 ValueError | ✓ main 模板分支 try/except，输出「模板 X 未注册」 |
| 模板 SQL 语法错误 | 模板 SQL 有误 | 数据库抛错 | ✓ 捕获 Exception，输出「数据库或 SQL 执行异常」 |
| 表/列不存在 | 模板引用了不存在的表或列 | 数据库抛错 | ✓ 同上 |

### 类型 D：时间/参数边界

| 案例 | 场景 | 处理方式 | 状态 |
|------|------|----------|------|
| 日期超出数据范围 | 用户问 2025-01-01 | extract_date 返回该日期；SQL 可能返回空 | 依赖类型 B 空结果兜底 |
| 无明确日期 | 「最近转化如何」 | get_default_dt() 取数据最新日 | ✓ 已覆盖 |
| 月日无年 | 「12-03 转化下降」 | extract_date 用数据年份推断 | ✓ 已覆盖 |
| 天数超限 | 「最近 999 天」 | extract_days 上限 90 天 | ✓ 已覆盖 |

### 类型 E：Intent 数据未就绪（intent_data_status）

| 案例 | 示例 | INTENT_DATA_STATUS | 当前行为 | 状态 |
|------|------|--------------------|----------|------|
| category_contrib_buyers | 哪些类目导致买家下降 | TYPE_E（需 category_id） | run_template 若模板不存在会抛错；若有模板但无数据则空结果 | 依赖 B/C |
| page_load_time_conversion | 加载时间对转化的影响 | TYPE_E（需 load_time_sec 等） | report 返回「未配置」类说明 | ✓ 有兜底 |
| region_conversion_diff | 地区转化差异 | TYPE_E（需 region_id） | report 返回「未配置」类说明 | ✓ 有兜底 |
| user_level_conversion | 用户分层转化 | TYPE_E（需 user_level） | 走模板，若模板无数据则空 | 依赖 B |

### 类型 F：无法识别的意图

| 案例 | 示例 | 当前行为 | 状态 |
|------|------|----------|------|
| 完全无关问题 | 「今天天气怎样」 | 落入 unrecognized，输出「暂不支持此类问题」 | ✓ 已实现（无数据关键词则 unrecognized） |
| 模糊/歧义问题 | 「看一下」等无数据关键词 | 落入 unrecognized | ✓ 已实现 |

### 类型 G：慢查询

| 案例 | 场景 | 处理方式 | 状态 |
|------|------|----------|------|
| 查询超时 | 大表全表扫描 | 无超时设置 | ❌ 未实现 |

---

## 待补齐项

1. ~~**NOT_SUPPORTED（GMV/订单数/ARPU）**~~ ✓ 已实现
2. ~~**SQL 失败兜底**~~ ✓ 已实现
3. ~~**无法识别意图**~~ ✓ 已实现（无数据关键词 → unrecognized）
4. **慢查询**（可选）：对 run_template/run_sql 增加超时与可解释提示。
