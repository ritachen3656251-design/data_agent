# run_eval_multiturn.py
# 多轮对话评估：上下文保持、对话偏差
# 记录失败案例并分类统计

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mapper import map_query
from planner import plan_from_slots


def _load_cases(path: str | Path | None = None) -> list[dict]:
    if path is None:
        path = Path(__file__).resolve().parent / "eval_multiturn.jsonl"
    cases = []
    p = Path(path)
    if not p.exists():
        return cases
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(json.loads(line))
    return cases


def _build_mock_session(slots: dict, plan: dict, answer_obj: dict | None = None, prev_session: dict | None = None) -> dict:
    """根据 turn 的 slots+plan 构造下一轮可用的 session_ctx。连续单日时保留 prev_dt。"""
    ctx = {}
    prev = prev_session or {}
    if slots.get("dt"):
        if prev.get("last_dt") and prev["last_dt"] != slots["dt"] and (
            prev.get("last_intent") == "overview_day" or "overview_day" in (prev.get("last_tool_keys") or [])
        ):
            ctx["prev_dt"] = prev["last_dt"]
        ctx["last_dt"] = slots["dt"]
    if slots.get("days") is not None:
        ctx["last_days"] = int(slots["days"])
    calls = plan.get("calls") or []
    tool_keys = [c.get("tool_key") or c.get("tool") for c in calls]
    ctx["last_intent"] = slots.get("intent") or (tool_keys[0] if tool_keys else None)
    ctx["last_tool_keys"] = tool_keys
    if slots.get("metric_focus"):
        ctx["last_metric_focus"] = slots["metric_focus"]
    return ctx


def _plan_match(plan: dict, expected: dict) -> tuple[bool, str]:
    """检查 plan 是否符合预期。"""
    exp = expected or {}
    if exp.get("calls_contain"):
        actual = [c.get("tool_key") or c.get("tool") for c in (plan.get("calls") or [])]
        ok = all(t in actual for t in exp["calls_contain"])
        return ok, f"calls_contain {exp['calls_contain']} => {ok} (got {actual})"
    if exp.get("calls"):
        actual = plan.get("calls") or []
        exp_calls = exp["calls"]
        if len(actual) < len(exp_calls):
            return False, f"calls count {len(actual)} < {len(exp_calls)}"
        for i, ec in enumerate(exp_calls):
            ac = actual[i] if i < len(actual) else {}
            if (ac.get("tool_key") or ac.get("tool")) != ec.get("tool_key"):
                return False, f"call[{i}] mismatch"
            exp_params = ec.get("params") or {}
            for k, v in exp_params.items():
                if ac.get("params", {}).get(k) != v:
                    return False, f"call[{i}].params.{k} mismatch"
        return True, "calls match"
    return True, "no plan check"


def _extract_dt_from_plan(plan: dict) -> str | None:
    """从 plan 中提取使用的 dt（取首个 call 的 params.dt）。"""
    calls = plan.get("calls") or []
    for c in calls:
        dt = c.get("params", {}).get("dt")
        if dt:
            return dt
    return None


def _extract_days_from_plan(plan: dict) -> int | None:
    """从 plan 中提取使用的 days。"""
    calls = plan.get("calls") or []
    for c in calls:
        days = c.get("params", {}).get("days")
        if days is not None:
            return int(days)
    return None


def _check_context_use(turn_idx: int, slots: dict, plan: dict, session_ctx: dict, checks: list) -> list[tuple[bool, str]]:
    """检查本轮是否正确使用上下文。"""
    results = []
    for ck in checks:
        if ck.get("turn") != turn_idx:
            continue
        field = ck.get("field")
        expected = ck.get("expected")
        contains = ck.get("contains")
        from_session = ck.get("from_session")

        if field == "dt":
            actual = slots.get("dt") or _extract_dt_from_plan(plan)
            session_val = session_ctx.get(from_session or "last_dt") if from_session else None
            if expected and actual == expected:
                results.append((True, f"dt={actual} (from session)" if session_val else f"dt={actual}"))
            elif expected and actual != expected:
                results.append((False, f"dt expected {expected}, got {actual}"))
            elif from_session and session_val and actual == session_val:
                results.append((True, f"dt={actual} from session"))
            else:
                results.append((False, f"dt: expected {expected}, got {actual}, session had {session_val}"))

        elif field == "days":
            actual = slots.get("days") or _extract_days_from_plan(plan)
            session_val = session_ctx.get(from_session or "last_days") if from_session else None
            if expected is not None and actual == expected:
                results.append((True, f"days={actual}"))
            elif expected is not None and actual != expected:
                results.append((False, f"days expected {expected}, got {actual}"))
            elif from_session and session_val is not None and actual == session_val:
                results.append((True, f"days={actual} from session"))
            else:
                results.append((False, f"days: expected {expected}, got {actual}, session had {session_val}"))

        elif field == "prev_dt":
            actual = slots.get("prev_dt")
            session_val = session_ctx.get(from_session or "prev_dt") if from_session else None
            if expected and actual == expected:
                results.append((True, f"prev_dt={actual}" + (" from session" if session_val else "")))
            elif expected and actual != expected:
                results.append((False, f"prev_dt expected {expected}, got {actual}"))
            else:
                results.append((False, f"prev_dt: expected {expected}, got {actual}"))

        elif field == "assumptions" and contains:
            assumptions = slots.get("assumptions") or []
            txt = " ".join(str(a) for a in assumptions)
            ok = contains in txt
            results.append((ok, f"assumptions contains '{contains}' => {ok}"))

    return results


def _check_drift(turn_data: list[dict], drift_checks: list) -> list[tuple[bool, str]]:
    """检查对话偏差：多轮间 dt/days 是否一致。"""
    results = []
    for dc in drift_checks or []:
        ttype = dc.get("type", "")
        turns = dc.get("turns", [])
        expect_same = dc.get("expect_same", "")

        if expect_same == "dt":
            dts = []
            for ti in turns:
                idx = ti - 1 if ti > 0 else ti
                if idx < len(turn_data):
                    td = turn_data[idx]
                    dt = td.get("slots", {}).get("dt") or _extract_dt_from_plan(td.get("plan", {}))
                    dts.append((ti, dt))
            if len(set(d for _, d in dts if d)) <= 1:
                results.append((True, f"dt consistent across turns: {dts}"))
            else:
                results.append((False, f"dt inconsistent: {dts}"))

        elif expect_same == "days":
            days_list = []
            for ti in turns:
                idx = ti - 1 if ti > 0 else ti
                if idx < len(turn_data):
                    td = turn_data[idx]
                    days = td.get("slots", {}).get("days") or _extract_days_from_plan(td.get("plan", {}))
                    days_list.append((ti, days))
            if len(set(d for _, d in days_list if d is not None)) <= 1:
                results.append((True, f"days consistent: {days_list}"))
            else:
                results.append((False, f"days inconsistent: {days_list}"))

    return results


FAIL_CONTEXT = "上下文保持失败"
FAIL_DRIFT = "对话偏差失败"


def run_multiturn_eval(
    cases_path: str | Path | None = None,
    use_real_session: bool = False,
    failures_out_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    运行多轮对话评估。
    use_real_session=True 时需 DB，用真实 answer()+session；否则模拟 session_ctx。
    """
    if failures_out_path is None:
        failures_out_path = Path(__file__).resolve().parent / "eval_multiturn_failures.json"
    cases = _load_cases(cases_path)
    results = []

    for case in cases:
        case_id = case.get("id", "")
        turns_spec = case.get("turns", [])
        context_checks = case.get("context_checks", [])
        drift_checks = case.get("drift_checks", [])

        turn_data = []
        session_ctx = {}

        for i, ts in enumerate(turns_spec):
            q = ts.get("question", "")
            if use_real_session:
                try:
                    from memory import get_session
                    from agent.orchestrator import answer
                    session_id = f"eval_mt_{case_id}"
                    session_ctx = get_session(session_id)
                    out = answer(q, session_ctx=session_ctx, session_id=session_id)
                    slots = map_query(q, session_ctx)
                    plan = plan_from_slots(q, slots)
                    # 重新获取 session 供下一轮
                    session_ctx = get_session(session_id)
                except Exception as e:
                    turn_data.append({"slots": {}, "plan": {}, "error": str(e)})
                    continue
            else:
                slots = map_query(q, session_ctx)
                plan = plan_from_slots(q, slots)
                session_ctx = _build_mock_session(slots, plan, prev_session=session_ctx)

            turn_data.append({"slots": slots, "plan": plan, "session_after": dict(session_ctx)})

        # 评估
        ctx_ok = 0
        ctx_total = 0
        ctx_details = []
        for i, td in enumerate(turn_data):
            if td.get("error"):
                continue
            # 本轮用的 session = 上一轮结束后的 session
            prev_session = turn_data[i - 1].get("session_after", {}) if i > 0 else {}
            checks = _check_context_use(i + 1, td["slots"], td["plan"], prev_session, context_checks)
            for ok, msg in checks:
                ctx_total += 1
                if ok:
                    ctx_ok += 1
                ctx_details.append((ok, f"T{i+1} {msg}"))

        drift_ok = 0
        drift_total = 0
        drift_details = []
        for ok, msg in _check_drift(turn_data, drift_checks):
            drift_total += 1
            if ok:
                drift_ok += 1
            drift_details.append((ok, msg))

        # 首轮 intent/slots 正确性
        first_ok = True
        if turn_data and turns_spec:
            exp1 = turns_spec[0].get("expected_slots", {})
            s1 = turn_data[0].get("slots", {})
            if exp1.get("intent") and s1.get("intent") != exp1["intent"]:
                first_ok = False
            if exp1.get("dt") and exp1["dt"] != "exists" and s1.get("dt") != exp1["dt"]:
                first_ok = False
            if exp1.get("days") is not None and exp1.get("days") != "exists" and s1.get("days") != exp1["days"]:
                first_ok = False

        results.append({
            "id": case_id,
            "name": case.get("name", ""),
            "turns": turns_spec,
            "turn_data": turn_data,
            "context_accuracy": ctx_ok / ctx_total if ctx_total else 1.0,
            "context_correct": ctx_ok,
            "context_total": ctx_total,
            "context_details": ctx_details,
            "drift_accuracy": drift_ok / drift_total if drift_total else 1.0,
            "drift_correct": drift_ok,
            "drift_total": drift_total,
            "drift_details": drift_details,
            "first_turn_ok": first_ok,
        })

    # 汇总指标
    ctx_total = sum(r["context_total"] for r in results)
    ctx_ok = sum(r["context_correct"] for r in results)
    drift_total = sum(r["drift_total"] for r in results)
    drift_ok = sum(r["drift_correct"] for r in results)

    # 收集失败案例
    failures = []
    for r in results:
        ctx_fails = [msg for ok, msg in r.get("context_details", []) if not ok]
        drift_fails = [msg for ok, msg in r.get("drift_details", []) if not ok]
        if ctx_fails:
            turns = r.get("turn_data", [])
            turn_specs = r.get("turns", [])
            failures.append({
                "type": FAIL_CONTEXT,
                "id": r["id"],
                "name": r["name"],
                "input": [ts.get("question", "") for ts in turn_specs if ts.get("question")],
                "expected": "、".join(ctx_fails),
                "actual": [{"slots": td.get("slots"), "plan": {"calls": [{"tool_key": c.get("tool_key"), "params": c.get("params")} for c in (td.get("plan") or {}).get("calls") or []]}} for td in turns],
            })
        if drift_fails:
            failures.append({
                "type": FAIL_DRIFT,
                "id": r["id"],
                "name": r["name"],
                "input": [ts.get("question", "") for ts in r.get("turns", []) if ts.get("question")],
                "expected": "、".join(drift_fails),
                "actual": drift_fails,
            })

    if failures_out_path:
        try:
            with open(failures_out_path, "w", encoding="utf-8") as f:
                json.dump({
                    "failures": failures,
                    "summary": {
                        "context_fail": sum(1 for x in failures if x["type"] == FAIL_CONTEXT),
                        "drift_fail": sum(1 for x in failures if x["type"] == FAIL_DRIFT),
                    },
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return {
        "cases": results,
        "context_accuracy": ctx_ok / ctx_total if ctx_total else 1.0,
        "context_correct": ctx_ok,
        "context_total": ctx_total,
        "drift_accuracy": drift_ok / drift_total if drift_total else 1.0,
        "drift_correct": drift_ok,
        "drift_total": drift_total,
        "failures": failures,
    }


def _print_multiturn_failure_report(metrics: dict) -> None:
    """打印多轮失败报告。"""
    failures = metrics.get("failures") or []
    if not failures:
        print("无失败案例。\n")
        return

    total = metrics.get("context_total", 0) + metrics.get("drift_total", 0)
    fail_count = len(failures)
    fail_rate = fail_count / total if total else 0

    by_type = {}
    for f in failures:
        t = f.get("type", "其他")
        by_type.setdefault(t, []).append(f)

    print("=== 失败案例报告 ===\n")
    print(f"失败率 = {fail_count} 个用例失败 / {len(metrics.get('cases', []))} 个用例\n")
    print("各类失败数量：")
    for t, lst in sorted(by_type.items()):
        print(f"  {t}: {len(lst)}")
    print("\n示例案例：")
    for t in [FAIL_CONTEXT, FAIL_DRIFT]:
        lst = by_type.get(t, [])
        if not lst:
            continue
        f = lst[0]
        print(f"\n  [{t}] {f.get('id')} {f.get('name')}")
        inp = f.get("input")
        if inp:
            print(f"    输入：{inp}")
        print(f"    期望：{f.get('expected')}")
        print(f"    实际：{f.get('actual')}")


def main():
    import sys
    use_real = "--full" in sys.argv
    out_path = None if "--no-save" in sys.argv else Path(__file__).resolve().parent / "eval_multiturn_failures.json"
    metrics = run_multiturn_eval(use_real_session=use_real, failures_out_path=out_path)

    print("=== 多轮对话评估 ===\n")
    print(f"上下文保持正确率 = {metrics['context_correct']}/{metrics['context_total']} = {metrics['context_accuracy']:.2%}")
    print(f"对话偏差正确率   = {metrics['drift_correct']}/{metrics['drift_total']} = {metrics['drift_accuracy']:.2%}")
    if out_path:
        print(f"\n失败案例已保存至 {out_path}")
    print()

    for r in metrics["cases"]:
        status = "✓" if r["context_accuracy"] == 1 and r["drift_accuracy"] == 1 else "✗"
        print(f"  {status} {r['id']} {r['name']}")
        for ok, msg in r.get("context_details", []):
            print(f"      {'✓' if ok else '✗'} {msg}")
        for ok, msg in r.get("drift_details", []):
            print(f"      {'✓' if ok else '✗'} {msg}")

    _print_multiturn_failure_report(metrics)
    if not use_real:
        print("（使用模拟 session，加 --full 可跑真实 answer+session，需 DB）")


if __name__ == "__main__":
    main()
