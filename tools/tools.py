# tools.py
# 汇总表读查询工具：每个工具干一件事，输入清晰、输出稳定，自带防慢/防错护栏

from __future__ import annotations

import re
from typing import Any

import pandas as pd
from sqlalchemy import text

from . import db


# ========== 护栏常量 ==========
DAYS_MIN = 1
DAYS_MAX = 90
QUERY_TIMEOUT_MS = 15000  # 15 秒
DEFAULT_DAYS = 9
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _ensure_engine():
    if db.engine is None:
        raise RuntimeError("db.engine 未初始化，请先调用 starter.init()")


def _get_default_dt() -> str:
    return db.get_default_dt()


def _clamp_days(days: int) -> int:
    return max(DAYS_MIN, min(DAYS_MAX, int(days)))


def _escape_cast(sql: str) -> str:
    """将 PostgreSQL :: 类型转换转义，避免 SQLAlchemy 将 : 解析为参数。"""
    return sql.replace("::", "\\:\\:")


def _validate_dt(dt: str | None) -> str:
    if not dt:
        return _get_default_dt()
    if not DATE_PATTERN.match(dt):
        raise ValueError(f"dt 格式须为 YYYY-MM-DD，当前: {dt}")
    return dt


def _execute_with_guard(sql: str, params: dict[str, Any]) -> pd.DataFrame:
    """执行 SQL，带超时与错误处理。"""
    _ensure_engine()
    conn = db.engine.connect()
    try:
        conn.execute(text(f"SET LOCAL statement_timeout = '{QUERY_TIMEOUT_MS}'"))
        df = pd.read_sql(text(sql), conn, params=params)
        return df
    except Exception as e:
        err = str(e).lower()
        if "timeout" in err or "canceling" in err:
            raise RuntimeError(f"查询超时（{QUERY_TIMEOUT_MS/1000:.0f}s），请缩小时间范围") from e
        if "does not exist" in err or "relation" in err:
            raise RuntimeError(f"表或列不存在，请检查数据配置: {e}") from e
        raise
    finally:
        conn.close()


# ========== 工具 1：最近 N 天核心指标趋势 ==========

def get_overview_daily(*, days: int = 9) -> pd.DataFrame:
    """
    最近 N 天核心指标趋势。
    输入: days (1–90)，默认 9
    输出: DataFrame [dt, pv, uv, buyers]
    """
    days = _clamp_days(days)
    sql = """
    SELECT dt, pv, uv, buyers
    FROM ub.daily_metrics
    ORDER BY dt DESC
    LIMIT :days
    """
    df = _execute_with_guard(sql, {"days": days})
    df["dt"] = pd.to_datetime(df["dt"]).dt.strftime("%Y-%m-%d")
    return df


# ========== 工具 2：单日核心指标 ==========

def get_overview_day(*, dt: str | None = None) -> pd.DataFrame:
    """
    单日核心指标（全指标）。
    输入: dt (YYYY-MM-DD)，缺省取数据最新日
    输出: DataFrame [dt, pv, uv, buyers, cart_users, ...]
    """
    dt = _validate_dt(dt)
    sql = """
    SELECT *
    FROM ub.daily_metrics
    WHERE dt = CAST(:dt AS date)
    LIMIT 1
    """
    df = _execute_with_guard(sql, {"dt": dt})
    if not df.empty:
        df["dt"] = pd.to_datetime(df["dt"]).dt.strftime("%Y-%m-%d")
    return df


# ========== 工具 3：最近 N 天漏斗（含转化率） ==========

def get_funnel_daily(*, days: int = 9, end_dt: str | None = None) -> pd.DataFrame:
    """
    最近 N 天漏斗，含 uv_to_buyer、uv_to_cart、cart_to_buyer。
    输入: days (1–90)，默认 9；end_dt 可选，指定截止日则取该日及前 N-1 天
    输出: DataFrame [dt, pv, uv, buyers, cart_users, uv_to_buyer, uv_to_cart, cart_to_buyer]
    """
    days = _clamp_days(days)
    if end_dt and DATE_PATTERN.match(str(end_dt)[:10]):
        sql = """
        SELECT dt, pv, uv, buyers, cart_users,
          CASE WHEN uv > 0 THEN buyers::numeric / uv ELSE 0 END AS uv_to_buyer,
          CASE WHEN uv > 0 THEN cart_users::numeric / uv ELSE 0 END AS uv_to_cart,
          CASE WHEN cart_users > 0 THEN buyers::numeric / cart_users ELSE 0 END AS cart_to_buyer
        FROM ub.daily_metrics
        WHERE dt <= CAST(:end_dt AS date)
        ORDER BY dt DESC
        LIMIT :days
        """
        df = _execute_with_guard(_escape_cast(sql), {"days": days, "end_dt": end_dt[:10]})
    else:
        sql = """
        SELECT dt, pv, uv, buyers, cart_users,
          CASE WHEN uv > 0 THEN buyers::numeric / uv ELSE 0 END AS uv_to_buyer,
          CASE WHEN uv > 0 THEN cart_users::numeric / uv ELSE 0 END AS uv_to_cart,
          CASE WHEN cart_users > 0 THEN buyers::numeric / cart_users ELSE 0 END AS cart_to_buyer
        FROM ub.daily_metrics
        ORDER BY dt DESC
        LIMIT :days
        """
        df = _execute_with_guard(_escape_cast(sql), {"days": days})
    df["dt"] = pd.to_datetime(df["dt"]).dt.strftime("%Y-%m-%d")
    return df


# ========== 工具 4：用户留存（次日留存率） ==========

def get_user_retention(*, days: int = 7) -> pd.DataFrame:
    """
    最近 N 天次日留存率。
    输入: days (1–90)，默认 7
    输出: DataFrame [dt, retention_1d]
    依赖: ub.user_behavior (user_id, dt)
    """
    days = _clamp_days(days)
    sql = """
    WITH base AS (
      SELECT (dt::date) AS dt, user_id
      FROM ub.user_behavior
      GROUP BY dt::date, user_id
    ),
    ret AS (
      SELECT
        a.dt,
        (COUNT(DISTINCT b.user_id)::float / NULLIF(COUNT(DISTINCT a.user_id), 0)) AS retention_1d
      FROM base a
      LEFT JOIN base b ON a.user_id = b.user_id AND b.dt = a.dt + 1
      GROUP BY a.dt
    )
    SELECT * FROM ret ORDER BY dt DESC LIMIT :days
    """
    df = _execute_with_guard(_escape_cast(sql), {"days": days})
    df["dt"] = pd.to_datetime(df["dt"]).dt.strftime("%Y-%m-%d")
    return df


# ========== 工具 5：用户活跃度 DAU ==========

def get_user_activity(*, days: int = 7) -> pd.DataFrame:
    """
    最近 N 天日活（DAU）。
    输入: days (1–90)，默认 7
    输出: DataFrame [dt, dau]
    依赖: ub.user_behavior (user_id, dt)
    """
    days = _clamp_days(days)
    sql = """
    SELECT dt::date AS dt, COUNT(DISTINCT user_id) AS dau
    FROM ub.user_behavior
    GROUP BY dt::date
    ORDER BY dt DESC
    LIMIT :days
    """
    df = _execute_with_guard(_escape_cast(sql), {"days": days})
    df["dt"] = pd.to_datetime(df["dt"]).dt.strftime("%Y-%m-%d")
    return df


# ========== 工具 6：类目贡献（buyers 变化） ==========

def get_category_contrib_buyers(*, dt: str | None = None) -> pd.DataFrame:
    """
    某日各类目买家数及相对前日变化。
    输入: dt (YYYY-MM-DD)，缺省取数据最新日
    输出: DataFrame [category_id, buyers_cur, buyers_prev, delta]
    依赖: ub.user_behavior 需有 category_id；若无则返回空
    """
    dt = _validate_dt(dt)
    # 需 user_behavior 有 category_id，且能区分 buy 行为
    sql = """
    WITH cur AS (
      SELECT category_id, COUNT(DISTINCT user_id) AS buyers_cur
      FROM ub.user_behavior
      WHERE dt::date = :dt::date
        AND (behavior_type = 'buy' OR behavior_type = 'pay')
        AND category_id IS NOT NULL
      GROUP BY category_id
    ),
    prev AS (
      SELECT category_id, COUNT(DISTINCT user_id) AS buyers_prev
      FROM ub.user_behavior
      WHERE dt::date = (:dt::date - 1)
        AND (behavior_type = 'buy' OR behavior_type = 'pay')
        AND category_id IS NOT NULL
      GROUP BY category_id
    )
    SELECT
      COALESCE(c.category_id, p.category_id) AS category_id,
      COALESCE(c.buyers_cur, 0)::int AS buyers_cur,
      COALESCE(p.buyers_prev, 0)::int AS buyers_prev,
      (COALESCE(c.buyers_cur, 0) - COALESCE(p.buyers_prev, 0))::int AS delta
    FROM cur c
    FULL OUTER JOIN prev p ON c.category_id = p.category_id
    ORDER BY delta DESC NULLS LAST
    LIMIT 500
    """
    try:
        df = _execute_with_guard(_escape_cast(sql), {"dt": dt})
        return df
    except Exception as e:
        if "column" in str(e).lower() and "category_id" in str(e).lower():
            return pd.DataFrame(columns=["category_id", "buyers_cur", "buyers_prev", "delta"])
        raise


# ========== 工具 7：新老用户转化率 ==========

def get_new_vs_old_conversion(*, dt: str | None = None) -> pd.DataFrame:
    """
    某日新老用户转化率。
    输入: dt (YYYY-MM-DD)，缺省取数据最新日
    输出: DataFrame [dt, new_cvr, old_cvr, new_uv, old_uv, new_buyers, old_buyers]
    依赖: ub.user_behavior (user_id, dt, behavior_type)
    """
    dt = _validate_dt(dt)
    sql = """
    WITH first_visit AS (
      SELECT user_id, MIN(dt::date) AS first_dt
      FROM ub.user_behavior
      GROUP BY user_id
    ),
    day_users AS (
      SELECT u.dt::date AS dt, u.user_id,
        CASE WHEN u.dt::date = f.first_dt THEN 'new' ELSE 'old' END AS segment
      FROM ub.user_behavior u
      JOIN first_visit f ON u.user_id = f.user_id
      WHERE u.dt::date = :dt::date
    ),
    buyers AS (
      SELECT user_id, dt::date AS dt FROM ub.user_behavior
      WHERE behavior_type = 'buy' OR behavior_type = 'pay'
    ),
    agg AS (
      SELECT d.dt,
        COUNT(DISTINCT CASE WHEN d.segment = 'new' THEN d.user_id END) AS new_uv,
        COUNT(DISTINCT CASE WHEN d.segment = 'old' THEN d.user_id END) AS old_uv,
        COUNT(DISTINCT CASE WHEN d.segment = 'new' AND b.user_id IS NOT NULL THEN d.user_id END) AS new_buyers,
        COUNT(DISTINCT CASE WHEN d.segment = 'old' AND b.user_id IS NOT NULL THEN d.user_id END) AS old_buyers
      FROM day_users d
      LEFT JOIN buyers b ON d.user_id = b.user_id AND d.dt = b.dt
      GROUP BY d.dt
    )
    SELECT dt,
      CASE WHEN new_uv > 0 THEN new_buyers::float / new_uv ELSE 0 END AS new_cvr,
      CASE WHEN old_uv > 0 THEN old_buyers::float / old_uv ELSE 0 END AS old_cvr,
      new_uv, old_uv, new_buyers, old_buyers
    FROM agg
    LIMIT 1
    """
    df = _execute_with_guard(_escape_cast(sql), {"dt": dt})
    if not df.empty:
        df["dt"] = pd.to_datetime(df["dt"]).dt.strftime("%Y-%m-%d")
    return df


# ========== 工具映射（供 executor 调用） ==========

TOOL_REGISTRY: dict[str, callable] = {
    "overview_daily": lambda **kw: get_overview_daily(**{k: v for k, v in kw.items() if k in ("days",)}),
    "overview_day": lambda **kw: get_overview_day(**{k: v for k, v in kw.items() if k in ("dt",)}),
    "funnel_daily": lambda **kw: get_funnel_daily(**{k: v for k, v in kw.items() if k in ("days", "end_dt")}),
    "user_retention": lambda **kw: get_user_retention(**{k: v for k, v in kw.items() if k in ("days",)}),
    "user_activity": lambda **kw: get_user_activity(**{k: v for k, v in kw.items() if k in ("days",)}),
    "category_contrib_buyers": lambda **kw: get_category_contrib_buyers(**{k: v for k, v in kw.items() if k in ("dt",)}),
    "new_vs_old_user_conversion": lambda **kw: get_new_vs_old_conversion(**{k: v for k, v in kw.items() if k in ("dt",)}),
}


def run_tool(tool_key: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    """
    统一入口：按 tool_key 调用对应工具。
    params 会做 clamp/默认值处理。
    """
    params = params or {}
    fn = TOOL_REGISTRY.get(tool_key)
    if not fn:
        raise ValueError(f"未知工具: {tool_key}。可用: {list(TOOL_REGISTRY.keys())}")

    # 补默认值
    if tool_key in ("overview_daily", "funnel_daily") and "days" not in params:
        params["days"] = DEFAULT_DAYS
    if tool_key in ("overview_day", "category_contrib_buyers", "new_vs_old_user_conversion") and "dt" not in params:
        params["dt"] = _get_default_dt()
    if tool_key in ("user_retention", "user_activity") and "days" not in params:
        params["days"] = 7

    return fn(**params)
