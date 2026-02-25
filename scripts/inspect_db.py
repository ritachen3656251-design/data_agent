#!/usr/bin/env python3
# 检查 Postgres 中 ub schema 的表结构、数据量、字段填充情况

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in path when run as script
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def main():
    try:
        from agent.starter import init
        init()
    except Exception as e:
        print(f"初始化失败: {e}")
        sys.exit(1)

    from tools import db
    from sqlalchemy import text
    import pandas as pd

    engine = db.engine
    if not engine:
        print("db.engine 未初始化")
        sys.exit(1)

    results = []

    with engine.connect() as conn:
        # 1. 列出 ub schema 下的所有表
        tables_df = pd.read_sql(text("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = 'ub'
            ORDER BY table_name
        """), conn)
        tables = tables_df.to_dict("records") if not tables_df.empty else []
        if not tables:
            print("ub schema 下无表，或 schema 不存在")
            return
        print("=" * 60)
        print("ub schema 下的表:", [t["table_name"] for t in tables])
        print()

        for tbl in tables:
            tname = f"ub.{tbl['table_name']}"
            print("=" * 60)
            print(f"表: {tname}")
            print("-" * 60)

            # 2. 列信息
            cols_df = pd.read_sql(text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'ub' AND table_name = :tname
                ORDER BY ordinal_position
            """), conn, params={"tname": tbl["table_name"]})
            columns = [r["column_name"] for r in cols_df.to_dict("records")]
            print("列:", columns)
            print()

            # 3. 行数
            cnt_df = pd.read_sql(text(f"SELECT COUNT(*) AS cnt FROM {tname}"), conn)
            row_count = int(cnt_df["cnt"].iloc[0])
            print(f"行数: {row_count}")

            if row_count == 0:
                print("  [无数据]")
                results.append({"table": tname, "rows": 0, "columns": columns, "fill_status": "空表"})
                print()
                continue

            # 4. 日期范围（若有 dt 列）
            if "dt" in columns:
                range_df = pd.read_sql(text(f"""
                    SELECT MIN(dt)::text AS min_dt, MAX(dt)::text AS max_dt FROM {tname}
                """), conn)
                r = range_df.iloc[0]
                print(f"dt 范围: {r['min_dt'][:10] if r['min_dt'] else 'N/A'} ~ {r['max_dt'][:10] if r['max_dt'] else 'N/A'}")

            # 5. 每列非空率
            print("\n字段填充情况:")
            fill_info = []
            for col in columns:
                try:
                    stat = pd.read_sql(text(f"""
                        SELECT
                            COUNT(*) AS total,
                            COUNT({col}) AS non_null,
                            COUNT(DISTINCT {col}) AS distinct_vals
                        FROM {tname}
                    """), conn)
                    r = stat.iloc[0]
                    total = int(r["total"])
                    non_null = int(r["non_null"])
                    distinct = int(r["distinct_vals"])
                    pct = (non_null / total * 100) if total > 0 else 0
                    status = "有数据" if non_null > 0 else "全空"
                    fill_info.append({"col": col, "non_null": non_null, "total": total, "pct": pct, "distinct": distinct})
                    print(f"  {col}: {non_null}/{total} ({pct:.1f}%) 非空, {distinct} 个不同值  [{status}]")
                except Exception as e:
                    print(f"  {col}: 检查失败 - {e}")
                    fill_info.append({"col": col, "error": str(e)})
            results.append({"table": tname, "rows": row_count, "columns": columns, "fill_info": fill_info})

            # 6. 样本（前 2 行）
            sample = pd.read_sql(text(f"SELECT * FROM {tname} LIMIT 2"), conn)
            print("\n样本 (前2行):")
            print(sample.to_string())
            print()

    # 汇总：agent 可查询什么
    print("=" * 60)
    print("Agent 可查询的数据汇总")
    print("=" * 60)
    _print_agent_summary(results)
    return results


def _print_agent_summary(results: list) -> None:
    """根据检查结果汇总 agent 可用的数据。"""
    for r in results:
        tname = r.get("table", "")
        rows = r.get("rows", 0)
        cols = r.get("columns", [])
        fill_info = r.get("fill_info", [])

        if rows == 0:
            print(f"\n❌ {tname}: 无数据，对应工具会返回空")
            continue

        filled = [f["col"] for f in fill_info if isinstance(f, dict) and f.get("non_null", 0) > 0]
        empty = [f["col"] for f in fill_info if isinstance(f, dict) and f.get("non_null", 0) == 0]

        print(f"\n✅ {tname}: {rows} 行")
        if filled:
            print(f"   有数据字段: {', '.join(filled)}")
        if empty:
            print(f"   待填充/全空: {', '.join(empty)}")

    # 按 tools 依赖说明
    print("\n" + "-" * 60)
    print("按工具依赖:")
    print("  overview_day/daily, funnel_daily → ub.daily_metrics (dt, pv, uv, buyers, cart_users)")
    print("  user_retention, user_activity → ub.user_behavior (user_id, dt)")
    print("  category_contrib_buyers → ub.user_behavior 需 category_id, behavior_type")
    print("  new_vs_old_user_conversion → ub.user_behavior (user_id, dt, behavior_type)")


if __name__ == "__main__":
    main()
