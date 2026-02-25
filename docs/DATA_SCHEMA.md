# 数据 Schema 说明

## 一、汇总表 / 基础表

### 1. ub.daily_metrics（日维度汇总表）

| 字段 | 类型 | 口径 |
|------|------|------|
| **dt** | date | 日期，格式 `YYYY-MM-DD` |
| **pv** | 数值 | 页面浏览次数（点击/浏览） |
| **uv** | 数值 | 独立访客数（去重 user_id） |
| **buyers** | 数值 | 有购买行为的去重用户数 |
| **cart_users** | 数值 | 有加购行为的去重用户数 |

**说明**：由 `ub.user_behavior` 按日聚合得到；overview_daily / funnel_daily 等模板直接查此表。

---

### 2. ub.user_behavior（用户行为明细表）

| 字段 | 类型 | 口径 |
|------|------|------|
| **dt** 或 **timestamp** | date / timestamp | 行为发生日期或时间戳；代码中多用 `dt::date` |
| **user_id** | 整数 | 用户标识 |
| **item_id** | 整数 | 商品标识（可选） |
| **category_id** | 整数 | 商品类目 ID（可选，类目分析依赖） |
| **behavior_type** | 字符串 | 行为类型：`pv` / `buy` / `cart` / `fav` |

**说明**：对应天池淘宝用户行为数据集；dt 可由 timestamp 转换。user_retention、user_activity、new_vs_old_user_conversion、user_level_conversion 等均依赖此表。

---

### 3. daily_funnel（多日漏斗视图/模板输出）

**非物理表**，由 `ub.daily_metrics` 通过 SQL 模板计算得到。输出字段：

| 字段 | 口径 |
|------|------|
| dt | 日期 |
| pv, uv, buyers, cart_users | 同上 |
| uv_to_buyer | buyers/uv，粗转化率 |
| uv_to_cart | cart_users/uv，加购率 |
| cart_to_buyer | buyers/cart_users，加购到购买转化 |

---

### 4. category_contrib_buyers（类目贡献输出）

**非物理表**，需由 `ub.user_behavior` 按 category_id 聚合或单独建类目汇总表。期望输出：

| 字段 | 口径 |
|------|------|
| category_id | 类目 ID |
| buyers_cur | 当日买家数 |
| buyers_prev | 前一日买家数 |
| delta | buyers_cur - buyers_prev |

**说明**：多数天池数据集有 category_id，需自建 SQL 模板或汇总表；当前多为 TYPE_E（待配置）。

---

### 5. ub.sql_templates（SQL 模板表）

| 字段 | 说明 |
|------|------|
| template_key | 模板唯一键 |
| intent | 意图标签 |
| description | 描述 |
| sql | SQL 文本（支持 :dt, :days 等参数） |

---

## 二、时间字段

| 表/场景 | 字段 | 格式 |
|---------|------|------|
| ub.daily_metrics | **dt** | `YYYY-MM-DD` (date) |
| ub.user_behavior | **dt** 或 timestamp | `dt::date` 转成 `YYYY-MM-DD` |
| 参数传递 | **dt** | `YYYY-MM-DD` 字符串 |
| 区间 | **start**, **end** | `YYYY-MM-DD` |

**结论**：统一使用 **dt**，格式 `YYYY-MM-DD`；无 ts 字段。

---

## 三、指标字典（METRIC_DICT）

| key | 口径 | 同义词示例 |
|-----|------|------------|
| **pv** | 页面浏览次数 | 浏览量、访问量、点击、页面浏览 |
| **uv** | 去重访客数 | 访客、独立访客、用户数、流量 |
| **buyers** | 有购买行为的去重用户数 | 买家、成交用户、购买用户、买家数、成交量 |
| **cart_users** | 有加购行为的去重用户数 | 加购用户、购物车用户、cart |
| **uv_to_buyer** | buyers/uv，UV 到买家转化率 | 转化率、uv转化、访问到购买、粗转化率 |
| **uv_to_cart** | cart_users/uv | 加购率、访问到加购 |
| **cart_to_buyer** | buyers/cart_users | 加购到购买转化、购物车转化 |
| **retention_1d** | 次日留存率 | 次日留存、留存、留存率 |
| **retention_7d** | 7日留存率 | 7日留存、周留存 |
| **dau** | 日活跃用户数 | 日活、活跃用户、活跃度 |
| **new_cvr** | 新用户转化率 | 新用户转化 |
| **old_cvr** | 老用户转化率 | 老用户转化 |
| **cvr** | 转化率（通用） | 转化率、conversion_rate |
| **load_time_sec** | 页面加载时间(秒) | 页面加载时间、加载时间 |

---

## 四、不支持指标（NOT_SUPPORTED）

| 指标 | 同义词 | 缺失说明 |
|------|--------|----------|
| **gmv** | GMV、成交额、销售额、交易额、支付金额 | 本数据集无价格/支付金额字段（如 price, amount, pay_amt） |
| **order_count** | 订单数、订单量、下单数 | 本数据集无订单表或 order_id 维度 |
| **arpu** | ARPU、客单价、人均消费 | 本数据集无收入/金额字段 |

---

## 五、表依赖关系

```
ub.user_behavior (明细)
       │
       ├──► ub.daily_metrics (按日汇总，可自建)
       │
       ├──► user_retention (SQL 计算)
       ├──► user_activity (SQL 计算)
       ├──► new_vs_old_user_conversion (SQL 计算)
       └──► user_level_conversion (需 user_level 字段，多为 TYPE_E)

ub.daily_metrics
       │
       ├──► overview_daily, overview_day, funnel_daily
       └──► daily_funnel 模板输出 → 诊断/异常/预测

category_contrib_buyers
       └──► 需 user_behavior.category_id 或类目汇总表
```
