# plot_tools.py
# 绘图工具：plot_trend、plot_topn_bar，输出 png 路径或 bytes

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pandas as pd


def plot_trend(
    df: pd.DataFrame,
    x: str = "dt",
    ys: list[str] | None = None,
    title: str = "",
) -> str:
    """
    绘制趋势折线图。
    输入：df（需含 x 及 ys 列）、x 轴列名、ys 数值列列表、标题
    输出：png 文件路径
    df 为空时抛出 ValueError。
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        raise ValueError("plot_trend：df 为空，无法绘图")
    ys = ys or []
    if not ys:
        raise ValueError("plot_trend：ys 不能为空")
    missing = [c for c in [x] + ys if c not in df.columns]
    if missing:
        raise ValueError(f"plot_trend：缺少列 {missing}")

    # 按 x 轴升序排列，保证时间从左到右（工具返回 dt DESC，需反转）
    df = df.sort_values(x, ascending=True).reset_index(drop=True)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4))
    for col in ys:
        ax.plot(df[x].astype(str), df[col], marker="o", markersize=4, label=col)
    ax.set_xlabel(x)
    ax.set_ylabel(", ".join(ys))
    if title:
        ax.set_title(title)
    ax.legend(loc="best", fontsize=9)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    path = Path(tempfile.gettempdir()) / f"plot_trend_{uuid.uuid4().hex[:12]}.png"
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def plot_topn_bar(
    df: pd.DataFrame,
    x: str = "category_id",
    y: str = "delta",
    n: int = 10,
    title: str = "",
) -> str:
    """
    绘制 TopN 柱状图。
    输入：df（需含 x、y 列）、x 轴列、y 数值列、取前 n 条、标题
    输出：png 文件路径
    df 为空时抛出 ValueError。
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        raise ValueError("plot_topn_bar：df 为空，无法绘图")
    missing = [c for c in [x, y] if c not in df.columns]
    if missing:
        raise ValueError(f"plot_topn_bar：缺少列 {missing}")

    # 按 |y| 取 TopN，使正负 delta 都能体现“影响最大”
    sub = df.reindex(df[y].abs().sort_values(ascending=False).index).head(n)
    if sub.empty:
        raise ValueError("plot_topn_bar：筛选后无数据")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(range(len(sub)), sub[y], tick_label=sub[x].astype(str))
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    if title:
        ax.set_title(title)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    path = Path(tempfile.gettempdir()) / f"plot_topn_{uuid.uuid4().hex[:12]}.png"
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def _artifact_to_bytes(path: str) -> bytes:
    """将 png 路径转为 bytes（可选，供需要嵌入时使用）。"""
    with open(path, "rb") as f:
        return f.read()


def demo():
    """本地示例：仅当 __main__ 时运行，生产路径不调用。"""
    import pandas as pd

    # trend
    df_trend = pd.DataFrame({
        "dt": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        "uv": [1000, 1200, 1100, 1300],
        "buyers": [50, 60, 55, 65],
    })
    p1 = plot_trend(df_trend, x="dt", ys=["uv", "buyers"], title="UV / buyers trend")
    print(f"plot_trend 输出: {p1}")

    # topn bar
    df_bar = pd.DataFrame({
        "category_id": ["A", "B", "C", "D", "E", "F"],
        "delta": [100, -30, 80, -20, 60, 40],
    })
    p2 = plot_topn_bar(df_bar, x="category_id", y="delta", n=5, title="Category delta Top5")
    print(f"plot_topn_bar 输出: {p2}")

    # 空 df 错误
    try:
        plot_trend(pd.DataFrame(), ys=["uv"])
    except ValueError as e:
        print(f"空 df 预期错误: {e}")


if __name__ == "__main__":
    demo()
