# Data Agent 问题矩阵

本文件描述了 Data Agent 支持的核心问题，并对每个问题给出意图、模板、必填参数、输出结构等信息。

## 1️⃣ 描述型（What）

### 1.1 overview_daily
- **Intent**: overview_daily
- **示例问题**: 最近9天核心指标趋势？
- **依赖表**: daily_metrics
- **依赖指标**: pv/uv/buyers
- **输出结构**:
  - 结论：最近9天的核心指标趋势概览（表格展示）
  - 证据：每一天的 UV 和 Buyers 数据
  - 下一步：可以进一步查看转化率（`daily_funnel`）

### 1.2 overview_day
- **Intent**: overview_day
- **示例问题**: 12-03指标如何？
- **依赖表**: daily_metrics
- **依赖指标**: 全指标（pv、uv、buy_cnt 等）
- **输出结构**:
  - 结论：12-03 核心指标汇总
  - 证据：当天各指标值
  - 下一步：可以查看“漏斗转化”数据（`daily_funnel`）

### 1.3 funnel_daily
- **Intent**: funnel_daily
- **示例问题**: 最近漏斗情况？
- **依赖表**: daily_metrics
- **依赖指标**: 转化链（如 uv_to_buyer, cart_to_buyer）
- **输出结构**:
  - 结论：漏斗各环节的表现（例如：UV 到购买的转化率）
  - 证据：每一环节的具体数值
  - 下一步：可以查看具体的“买家”下降来源（`category_contrib`）

---

## 2️⃣ 诊断型（Why）

### 2.1 diagnose_conversion_drop
- **Intent**: diagnose_conversion_drop
- **示例问题**: 为什么12-03转化下降？
- **依赖指标**: uv, buyers, cart_users
- **诊断路径**:
  - 计算 `buyers/uv`（转化率）
  - 拆解流量（uv）和决策效率（buyers/cart_users）之间的变化

### 2.2 diagnose_uv_drop
- **Intent**: diagnose_uv_drop
- **示例问题**: 为什么UV下降？
- **依赖指标**: uv
- **诊断路径**:
  - 环比对比（与上一日UV对比，检查增长或下降的幅度）

### 2.3 diagnose_buyers_drop
- **Intent**: diagnose_buyers_drop
- **示例问题**: 为什么买家下降？
- **依赖指标**: buyers
- **诊断路径**:
  - 拆解为流量（uv）和决策效率（cart_users/total buyers）两部分
  - 检查“流量问题”还是“转化效率问题”

---

## 3️⃣ 归因型（Attribution）

### 3.1 category_contrib
- **Intent**: category_contrib
- **示例问题**: 哪些类目导致买家下降？
- **维度**: category_id
- **输出**:
  - delta排序：按每个类目贡献的买家数变化，从大到小列出

### 3.2 category_growth
- **Intent**: category_growth
- **示例问题**: 哪些类目拉动增长？
- **维度**: category_id
- **输出**:
  - 贡献率：按各个类目的贡献率从高到低展示

---

## 4️⃣ 异常型（Anomaly）

### 4.1 uv_anomaly
- **Intent**: uv_anomaly
- **示例问题**: 哪天UV异常？
- **方法**: 环比变化率
- **输出结构**:
  - 结论：显示UV异常波动的日期
  - 证据：计算并显示环比波动率，超出预设阈值的日期

### 4.2 buyers_anomaly
- **Intent**: buyers_anomaly
- **示例问题**: 买家异常？
- **方法**: Z-score
- **输出结构**:
  - 结论：哪些日期的买家数异常
  - 证据：Z-score 数值，超出阈值的日期

### 4.3 conversion_anomaly
- **Intent**: conversion_anomaly
- **示例问题**: 转化异常？
- **方法**: 波动阈值
- **输出结构**:
  - 结论：展示转化率异常的日期
  - 证据：计算波动阈值，超过阈值的日期