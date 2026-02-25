# run_all_regression.py
# 跑 mapper + planner 回归（只检查结构，不检查回答文本）

from __future__ import annotations

from .run_mapper_regression import main as main_mapper
from .run_planner_regression import main as main_planner


def main():
    print("=== Mapper 回归 ===")
    main_mapper()
    print("\n=== Planner 回归 ===")
    main_planner()


if __name__ == "__main__":
    main()
