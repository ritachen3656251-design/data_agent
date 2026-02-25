# db.py
# 数据库连接与基础查询：engine、时间范围、默认日期
# 供 tools.py 依赖

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd
from sqlalchemy import text

engine = None  # type: ignore  # 由 starter.init() 注入

_DEFAULT_DT_FALLBACK = "2017-12-03"
_data_date_range_cache: Optional[Tuple[str, str]] = None


def _ensure_engine() -> None:
    if engine is None:
        raise RuntimeError(
            "db.engine 未初始化，请先调用 starter.init()"
        )


def get_data_date_range() -> Tuple[str, str] | None:
    """
    从数据库获取数据实际时间范围 (min_dt, max_dt)。
    优先查 ub.daily_metrics，失败则查 ub.user_behavior。结果会缓存。
    """
    global _data_date_range_cache
    if _data_date_range_cache is not None:
        return _data_date_range_cache
    _ensure_engine()
    for tbl, col in [("ub.daily_metrics", "dt"), ("ub.user_behavior", "dt")]:
        try:
            sql = f"SELECT CAST(MIN({col}) AS text) AS min_dt, CAST(MAX({col}) AS text) AS max_dt FROM {tbl}"
            df = pd.read_sql(text(sql), engine)
            if df.empty or df["min_dt"].iloc[0] is None:
                continue
            min_dt = str(df["min_dt"].iloc[0])[:10]
            max_dt = str(df["max_dt"].iloc[0])[:10]
            _data_date_range_cache = (min_dt, max_dt)
            return _data_date_range_cache
        except Exception:
            continue
    return None


def get_default_dt() -> str:
    """返回默认查询日期：数据中最新的日期；无数据时用 fallback。"""
    r = get_data_date_range()
    return r[1] if r else _DEFAULT_DT_FALLBACK


def get_queryable_date_range() -> str:
    """返回可查询时间范围描述，供 LLM 提示用。"""
    r = get_data_date_range()
    if r:
        return f"{r[0]} 至 {r[1]}"
    return f"（数据库无数据，默认 {_DEFAULT_DT_FALLBACK}）"
