# Postgres 数据可用性报告

基于 `inspect_db.sql` 或 `inspect_db.py` 的检查结果整理。

---

## 一、ub schema 表概览

| 表名 | 行数 | Agent 是否使用 |
|------|------|----------------|
| **ub.daily_metrics** | 9 | ✅ 是 |
| **ub.user_behavior** | 98,147,813 | ✅ 是 |
| ub.daily_category_metrics | 73,521 | ❌ 否（预聚合类目表，可考虑接入） |
| ub.user_behavior_raw | 100,150,807 | ❌ 否 |
| ub.metrics_catalog | 9 | ❌ 否 |
| ub.sql_templates | 15 | ❌ 否 |

---

## 二、Agent 依赖表的数据与字段

### 1. ub.daily_metrics

| 字段 | 类型 | 填充情况 | 说明 |
|------|------|----------|------|
| dt | date | ✅ 9/9 | 日期 |
| pv | bigint | ✅ 9/9 | 页面浏览 |
| uv | bigint | ✅ 9/9 | 独立访客 |
| buyers | bigint | ✅ 9/9 | 购买人数 |
| cart_users | bigint | ✅ 9/9 | 加购人数 |
| fav_users | bigint | ✅ 9/9 | 收藏人数（Agent 未用） |
| buy_cnt | bigint | ✅ 9/9 | 购买笔数（Agent 未用） |

**日期范围**: 2017-11-25 ~ 2017-12-03（9 天）

**结论**: 核心指标字段全部有数据，Agent 的 overview_day、overview_daily、funnel_daily 均可正常使用。

---

### 2. ub.user_behavior

| 字段 | 类型 | 填充情况 | 说明 |
|------|------|----------|------|
| user_id | bigint | ✅ 全满 | 用户 ID |
| dt | date | ✅ 全满 | 日期 |
| behavior_type | text | ✅ 全满 | 行为类型 |
| category_id | bigint | ✅ 全满 | 类目 ID |
| item_id | bigint | ✅ 全满 | 商品 ID（Agent 未用） |
| ts | bigint | ✅ 全满 | 时间戳（Agent 未用） |
| hour | integer | ✅ 全满 | 小时（Agent 未用） |

**behavior_type 取值**: `pv`(8791万), `cart`(541万), `fav`(282万), `buy`(198万)

**注意**: Agent 的 `category_contrib_buyers`、`new_vs_old_user_conversion` 会过滤 `behavior_type IN ('buy','pay')`，当前数据只有 `buy`，无 `pay`，因此以 `buy` 为主，逻辑正常。

**日期范围**: 2017-11-25 ~ 2017-12-03

**结论**: 用户行为相关字段均非空，Agent 的 user_retention、user_activity、category_contrib_buyers、new_vs_old_user_conversion 均可正常使用。

---

## 三、Agent 可查询的数据

| 工具 | 依赖表 | 依赖字段 | 数据状态 |
|------|--------|----------|----------|
| **overview_day** | daily_metrics | dt, pv, uv, buyers, cart_users | ✅ 有数据 |
| **overview_daily** | daily_metrics | dt, pv, uv, buyers | ✅ 有数据 |
| **funnel_daily** | daily_metrics | dt, pv, uv, buyers, cart_users | ✅ 有数据 |
| **user_retention** | user_behavior | user_id, dt | ✅ 有数据 |
| **user_activity** | user_behavior | user_id, dt | ✅ 有数据 |
| **category_contrib_buyers** | user_behavior | user_id, dt, behavior_type, category_id | ✅ 有数据 |
| **new_vs_old_user_conversion** | user_behavior | user_id, dt, behavior_type | ✅ 有数据 |

---

## 四、待填充 / 未使用字段

| 表 | 字段 | 状态 |
|----|------|------|
| daily_metrics | fav_users, buy_cnt | 有数据，Agent 未使用 |
| user_behavior | item_id, ts, hour | 有数据，Agent 未使用 |
| daily_category_metrics | 整表 | 有 7.3 万行，Agent 未接入（可用于类目查询加速） |

当前无「待填充」字段，核心字段均有数据。

---

## 五、复现检查

```bash
# 需安装 psql，或确保 sqlalchemy+psycopg2 可用
PGPASSWORD=win123 psql -h 127.0.0.1 -U postgres -d tianchi_ub -f inspect_db.sql
# 或
python inspect_db.py
```
