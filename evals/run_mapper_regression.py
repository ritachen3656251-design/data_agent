# run_mapper_regression.py
# 加载 mapper_regression.jsonl，对 slots 执行 expect 断言（只检查结构，不检查回答文本）

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mapper import map_query


def _resolve_path(obj: Any, path: str) -> Any:
    """解析 path，如 intent、dt、not_supported.reason"""
    parts = re.split(r"\.", path)
    cur = obj
    for p in parts:
        m = re.match(r"(\w+)\[(\d+)\]", p)
        if m:
            key, idx = m.group(1), int(m.group(2))
            cur = cur.get(key) if isinstance(cur, dict) else None
            if cur is not None and isinstance(cur, list) and 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def _eval_expect(obj: Any, rule: dict) -> tuple[bool, str]:
    path = rule.get("path", "")
    op = rule.get("op", "eq")
    expected = rule.get("value")
    actual = _resolve_path(obj, path)

    if op == "exists":
        ok = actual is not None
        return ok, f"{path} exists={ok} (got {actual})"
    if op == "eq":
        ok = actual == expected
        return ok, f"{path} eq {expected} => {ok} (got {actual})"
    if op == "in":
        ok = actual in expected if isinstance(expected, (list, tuple)) else actual == expected
        return ok, f"{path} in {expected} => {ok} (got {actual})"
    if op == "len_gte":
        length = len(actual) if isinstance(actual, (list, tuple)) else 0
        ok = length >= expected if isinstance(expected, (int, float)) else False
        return ok, f"{path} len>={expected} => {ok} (got len={length})"
    return False, f"unknown op: {op}"


def run_mapper_regression(cases_path: str | Path | None = None) -> list[dict]:
    """执行 mapper 回归，返回 [{id, question, passed, failed_rules, slots}, ...]"""
    if cases_path is None:
        cases_path = Path(__file__).resolve().parent / "mapper_regression.jsonl"
    path = Path(cases_path)
    if not path.exists():
        return []
    results = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            case = json.loads(line)
            case_id = case.get("id", "")
            question = case.get("question", "")
            expect = case.get("expect") or []
            slots = map_query(question)
            failed = []
            for r in expect:
                ok, msg = _eval_expect(slots, r)
                if not ok:
                    failed.append({"rule": r, "msg": msg})
            results.append({
                "id": case_id,
                "question": question,
                "passed": len(failed) == 0,
                "failed_rules": failed,
                "slots": slots,
            })
    return results


def main():
    results = run_mapper_regression()
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"Mapper 回归: {passed}/{total} 通过")
    for r in results:
        status = "✓" if r["passed"] else "✗"
        print(f"  {status} {r['id']} {r['question'][:35]}...")
        if not r["passed"] and r.get("failed_rules"):
            for fr in r["failed_rules"]:
                print(f"      - {fr.get('msg', fr)}")


if __name__ == "__main__":
    main()
