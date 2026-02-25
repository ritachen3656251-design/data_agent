# main.py
# 最小闭环入口：answer(question) -> (text, charts)

from __future__ import annotations

import subprocess
import sys

from . import starter
from .orchestrator import answer


def _open_image(path: str) -> None:
    """用系统默认程序打开图片。"""
    try:
        if sys.platform == "win32":
            import os
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False, timeout=5)
        else:
            subprocess.run(["xdg-open", path], check=False, timeout=5)
    except Exception:
        pass


def main():
    starter.init()
    print("数据层已初始化。输入问题，回车得到回答。（q 退出）\n")
    while True:
        q = input("> ").strip()
        if q.lower() in ("q", "quit", "exit"):
            break
        if not q:
            continue
        try:
            text, charts = answer(q)
        except Exception as e:
            print(f"执行出错: {e}")
            continue
        print(text)
        if charts:
            paths = [c.get("path") for c in charts if c.get("path")]
            errors = [c.get("error") for c in charts if c.get("error")]
            for p in paths:
                print(f"[图表] {p}")
                _open_image(p)
            if errors and not paths:
                print("[图表] 无法绘图")
        print()


if __name__ == "__main__":
    main()
