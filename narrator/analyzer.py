# analyzer.py
# 从 DataFrame 提取结构化 insights，供 answer_obj 使用

from __future__ import annotations

from typing import Any

import pandas as pd


def _safe_float(x: Any) -> float:
    if x is None or (hasattr(x, "__float__") and pd.isna(x)):
        return 0.0
    try:
        return float(x)
    except (ValueError, TypeError):
        return 0.0


def _pct_change_pct(a: float, b: float) -> float | None:
    """返回 (b-a)/a*100，a=0 时返回 None。"""
    if a == 0 or a is None or pd.isna(a):
        return None
    return (float(b) - float(a)) / float(a) * 100


def analyze(df: pd.DataFrame, kind: str) -> list[dict[str, Any]]:
    """
    从 df 提取 insights，返回结构化 list。
    每条：{type: str, text: str, importance: str ("high"|"medium"|"low") + 可选数字字段}
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return []

    kind = (kind or "").strip().lower()
    if kind == "overview_daily":
        return _analyze_overview_daily(df)
    if kind == "funnel_daily":
        return _analyze_funnel_daily(df)
    if kind == "category_contrib_buyers":
        return _analyze_category_contrib_buyers(df)
    if kind in ("user_activity", "user_retention"):
        return _analyze_trend_with_inflection(df, kind)
    return []


def analyze_diagnose(
    overview_df: pd.DataFrame | None,
    funnel_df: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """
    诊断场景：从 overview + funnel 提取结构化 insights，供「为什么」类问题使用。
    返回：primary_cause（主因）、secondary_changes（其他环节）、key_metrics（UV/买家数）。
    用于构建「最可能的原因是…从 X% 降至 Y%，降幅 Z%。UV 为 A，买家数为 B…」式回答。
    """
    insights: list[dict[str, Any]] = []
    if funnel_df is None or (hasattr(funnel_df, "empty") and funnel_df.empty) or len(funnel_df) < 2:
        return insights

    latest = funnel_df.iloc[0]
    earliest = funnel_df.iloc[-1]
    cols = [
        ("uv_to_buyer", "UV 到购买转化率"),
        ("uv_to_cart", "加购率"),
        ("cart_to_buyer", "加购到购买转化率"),
    ]
    changes: list[tuple[float, str, str, float, float, float]] = []
    for col, label in cols:
        if col not in funnel_df.columns:
            continue
        ev = _safe_float(earliest.get(col))
        lv = _safe_float(latest.get(col))
        pct = _pct_change_pct(ev, lv)
        if pct is not None:
            changes.append((abs(pct), label, col, pct, ev, lv))

    changes.sort(reverse=True, key=lambda x: x[0])

    # 主因：变化幅度最大的环节
    if changes:
        _, label, col, pct, ev, lv = changes[0]
        direction = "上升" if pct > 0 else "下降"
        ev_pct = f"{ev:.2%}" if ev <= 1 else f"{ev:.2f}"
        lv_pct = f"{lv:.2%}" if lv <= 1 else f"{lv:.2f}"
        dir_word = "升至" if pct > 0 else "降至"
        pct_suffix = f"，升幅达{pct:.1f}%" if pct > 0 else f"，降幅达{abs(pct):.1f}%"
        insights.append({
            "type": "diagnose_primary",
            "text": f"最可能的原因是{label}{direction}导致整体表现波动，从{ev_pct}{dir_word}{lv_pct}{pct_suffix}",
            "importance": "high",
            "step": col,
            "change_pct": pct,
            "from_val": ev,
            "to_val": lv,
            "label": label,
        })

    # 次要变化：其他环节
    for t in changes[1:]:
        _, label, col, pct, ev, lv = t
        direction = "上升" if pct > 0 else "下降"
        ev_pct = f"{ev:.2%}" if ev <= 1 else f"{ev:.2f}"
        lv_pct = f"{lv:.2f}" if lv <= 1 else f"{lv:.2%}"
        if ev <= 1 and lv <= 1:
            ev_pct, lv_pct = f"{ev:.2%}", f"{lv:.2%}"
        qual = "略有" if abs(pct) < 3 else ("显著" if abs(pct) > 8 else "")
        dir_word = "升至" if pct > 0 else "降至"
        insights.append({
            "type": "diagnose_secondary",
            "text": f"{label}{qual}{direction}，从{ev_pct}{dir_word}{lv_pct}",
            "importance": "medium",
            "step": col,
            "change_pct": pct,
            "from_val": ev,
            "to_val": lv,
            "label": label,
        })

    # 关键指标：UV、买家数（取目标日/最新 overview，首行=最新）
    if overview_df is not None and not (hasattr(overview_df, "empty") and overview_df.empty):
        row = overview_df.iloc[0]
        uv = _safe_float(row.get("uv"))
        buyers = _safe_float(row.get("buyers"))
        if uv > 0 or buyers > 0:
            insights.append({
                "type": "diagnose_metrics",
                "text": f"UV 为 {int(uv)}，买家数为 {int(buyers)}",
                "importance": "high",
                "uv": int(uv),
                "buyers": int(buyers),
            })

    return insights


def _analyze_overview_daily(df: pd.DataFrame) -> list[dict[str, Any]]:
    """找最大/最小/最近变化、top swing day（按 uv/buyers）。"""
    insights = []
    if len(df) < 2:
        return insights

    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.sort_values("dt", ascending=True).reset_index(drop=True)

    for col, label in [("uv", "UV"), ("buyers", "买家数")]:
        if col not in df.columns:
            continue
        vals = df[col].dropna()
        if vals.empty:
            continue
        mx = vals.max()
        mn = vals.min()
        mx_row = df.loc[df[col].idxmax()] if not df.empty else None
        mn_row = df.loc[df[col].idxmin()] if not df.empty else None
        mx_dt = str(mx_row["dt"])[:10] if mx_row is not None else ""
        mn_dt = str(mn_row["dt"])[:10] if mn_row is not None else ""
        insights.append({
            "type": "extreme",
            "text": f"{label} 最大 {int(mx)} 出现在 {mx_dt}",
            "importance": "medium",
            "metric": col,
            "value": int(mx),
            "dt": mx_dt,
        })
        if mn != mx:
            insights.append({
                "type": "extreme",
                "text": f"{label} 最小 {int(mn)} 出现在 {mn_dt}",
                "importance": "low",
                "metric": col,
                "value": int(mn),
                "dt": mn_dt,
            })

    # 最近变化：首行 vs 末行
    latest = df.iloc[-1]
    earliest = df.iloc[0]
    for col, label in [("uv", "UV"), ("buyers", "买家数")]:
        if col not in df.columns:
            continue
        ev = _safe_float(earliest.get(col))
        lv = _safe_float(latest.get(col))
        pct = _pct_change_pct(ev, lv)
        if pct is not None:
            direction = "上升" if pct > 0 else "下降"
            insights.append({
                "type": "recent_change",
                "text": f"最近变化：{label} {direction} {pct:+.1f}%",
                "importance": "high",
                "metric": col,
                "change_pct": pct,
            })

    # top swing day：日环比变化最大的那天（按 uv 或 buyers）
    if len(df) >= 3 and "uv" in df.columns:
        df["uv_pct"] = df["uv"].pct_change() * 100
        df = df.dropna(subset=["uv_pct"])
        if not df.empty:
            idx = df["uv_pct"].abs().idxmax()
            row = df.loc[idx]
            dt_val = str(row["dt"])[:10]
            pct_val = float(row["uv_pct"])
            insights.append({
                "type": "top_swing_day",
                "text": f"UV 日环比波动最大日为 {dt_val}（{pct_val:+.1f}%）",
                "importance": "high",
                "dt": dt_val,
                "change_pct": pct_val,
            })

    return insights


def _analyze_funnel_daily(df: pd.DataFrame) -> list[dict[str, Any]]:
    """找变化最大的转化环节（uv_to_buyer/uv_to_cart/cart_to_buyer）。"""
    insights = []
    cols = [
        ("uv_to_buyer", "UV 到购买转化率"),
        ("uv_to_cart", "加购率"),
        ("cart_to_buyer", "加购到购买转化率"),
    ]
    if len(df) < 2:
        return insights

    latest = df.iloc[0]
    earliest = df.iloc[-1]
    changes = []
    for col, label in cols:
        if col not in df.columns:
            continue
        ev = _safe_float(earliest.get(col))
        lv = _safe_float(latest.get(col))
        pct = _pct_change_pct(ev, lv)
        if pct is not None:
            changes.append((abs(pct), label, col, pct, ev, lv))

    if changes:
        changes.sort(reverse=True, key=lambda x: x[0])
        top = changes[0]
        _, label, col, pct, ev, lv = top
        direction = "上升" if pct > 0 else "下降"
        insights.append({
            "type": "biggest_funnel_change",
            "text": f"变化最大环节：{label}，{ev:.2%} -> {lv:.2%}（{pct:+.1f}%）",
            "importance": "high",
            "step": col,
            "change_pct": pct,
            "from_val": ev,
            "to_val": lv,
        })

    return insights


def _analyze_category_contrib_buyers(df: pd.DataFrame) -> list[dict[str, Any]]:
    """输出 top5 delta、集中度（top1占比）。"""
    insights = []
    if df.empty or "delta" not in df.columns:
        return insights

    top5 = df.head(5)
    total_delta = df["delta"].abs().sum()
    if total_delta == 0:
        return insights

    # top5 delta
    for i, (_, r) in enumerate(top5.iterrows(), 1):
        cid = r.get("category_id", "")
        d = _safe_float(r.get("delta"))
        insights.append({
            "type": "top_delta",
            "text": f"Top{i} 类目 {cid} delta={int(d)}",
            "importance": "high" if i <= 2 else "medium",
            "rank": i,
            "category_id": str(cid),
            "delta": int(d),
        })

    # 集中度：top1 占 abs(delta) 总和的占比
    top1_abs = abs(_safe_float(top5.iloc[0].get("delta"))) if len(top5) > 0 else 0
    concentration = top1_abs / total_delta * 100 if total_delta else 0
    insights.append({
        "type": "concentration",
        "text": f"Top1 类目贡献占比 {concentration:.1f}%",
        "importance": "high",
        "top1_share_pct": round(concentration, 1),
    })

    return insights


def _analyze_trend_with_inflection(df: pd.DataFrame, kind: str) -> list[dict[str, Any]]:
    """user_activity/user_retention：输出趋势方向、拐点。"""
    insights = []
    if kind == "user_activity":
        col = "dau"
        label = "DAU"
    else:
        col = "retention_1d"
        label = "留存率"

    if col not in df.columns or len(df) < 2:
        return insights

    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.sort_values("dt", ascending=True).reset_index(drop=True)

    # 趋势方向
    first_val = _safe_float(df.iloc[0].get(col))
    last_val = _safe_float(df.iloc[-1].get(col))
    pct = _pct_change_pct(first_val, last_val)
    if pct is not None:
        direction = "上升" if pct > 0 else "下降"
        insights.append({
            "type": "trend_direction",
            "text": f"{label} 整体趋势{direction}（{pct:+.1f}%）",
            "importance": "high",
            "change_pct": pct,
            "direction": "up" if pct > 0 else "down",
        })

    # 拐点：一阶差分符号变化
    if len(df) >= 3:
        d = df[col].astype(float).diff()
        for i in range(2, len(d)):
            a, b = d.iloc[i - 1], d.iloc[i]
            if pd.isna(a) or pd.isna(b):
                continue
            if a * b < 0:
                dt_val = str(df.iloc[i]["dt"])[:10]
                insights.append({
                    "type": "inflection",
                    "text": f"{label} 在 {dt_val} 附近存在拐点",
                    "importance": "medium",
                    "dt": dt_val,
                })
                break

    return insights
