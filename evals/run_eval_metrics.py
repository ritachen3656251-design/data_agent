# run_eval_metrics.py
# 评估指标体系：意图、参数、模板、边界、幻觉
# 记录失败案例并分类统计

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mapper import map_query
from planner import plan_from_slots

# 失败类型
FAIL_INTENT = "意图识别失败"
FAIL_PARAM = "参数抽取失败"
FAIL_TEMPLATE = "模板命中失败"
FAIL_BOUNDARY = "边界处理失败"
FAIL_PLOT = "图表断言失败"
FAIL_HALLUCINATION = "幻觉"
FAIL_NOT_SUPPORTED = "暂时无法查询失败"

# 需要 trend 图的 tool
TOOLS_NEED_TREND = {"overview_daily", "funnel_daily", "user_retention", "user_activity"}

# 评估题目集划分
INTENT_POOL_IDS = [f"E{i:02d}" for i in range(1, 21)] + [f"B{i:02d}" for i in range(1, 11)]  # 30 题
# 新老转化/留存/日活/单独买家数 暂不查询 → 从成功池排除
INTENT_POOL_IDS = [x for x in INTENT_POOL_IDS if x not in ("E04", "E05", "B09", "B10")]
DATE_REGRESSION_IDS = ["D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11", "D12", "D13"]  # 11月25-12月3日全覆盖
FUNNEL_ISOLATION_IDS = ["FN01", "FN02", "FN03"]  # 漏斗隔离：问漏斗时不得返回核心指标
COMPLEX_IDS = ["CX01", "CX02"]  # 复杂问题：诊断+归因混杂
EXPLICIT_METRIC_IDS = ["PM01", "PM02"]  # 显式指标：问 PV/UV/买家数 应返回具体指标
TWO_DAY_DIAGNOSE_IDS = ["D2R01", "D2R02", "D2R03", "D2R04", "D2R05"]  # 两日诊断：日期范围用 days=2 不用 9
NOT_SUPPORTED_IDS = ["F01", "F02", "F03", "F04", "F05", "F09", "F10", "F11", "F12", "F13", "F14"]  # 需返回 not_supported（F06/F07/F08 空/无关问题单独处理）
INTENT_POOL_IDS = INTENT_POOL_IDS + DATE_REGRESSION_IDS + FUNNEL_ISOLATION_IDS + COMPLEX_IDS + EXPLICIT_METRIC_IDS + TWO_DAY_DIAGNOSE_IDS
BOUNDARY_FALLBACK_IDS = ["B05", "B06", "B03", "B07", "B10"]  # 5 题兜底
# 需要 dt 的 intent（单题只取第一个 tool 的 params）
INTENTS_NEED_DT = {"overview_day", "category_contrib_buyers", "new_vs_old_user_conversion"}
DIAGNOSE_NEED_DT = True  # diagnose 首 call 为 overview_day(dt)


def _load_cases(path: str | Path | None = None) -> list[dict]:
    if path is None:
        path = Path(__file__).resolve().parent / "eval_standard_20.jsonl"
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


def _get_case_by_id(cases: list[dict], cid: str) -> dict | None:
    for c in cases:
        if c.get("id") == cid:
            return c
    return None


def _plan_template_match(plan: dict, expected: dict) -> bool:
    """模板命中：calls 的 tool_key 与 expected 一致。支持 calls_must_not_contain（如漏斗隔离）、funnel_daily_days_must_be（两日诊断）。"""
    exp_plan = expected.get("expected_plan") or {}
    calls = plan.get("calls") or []
    actual = [c.get("tool_key") or c.get("tool") for c in calls]
    must_not = exp_plan.get("calls_must_not_contain") or []
    if must_not and any(t in actual for t in must_not):
        return False
    exp_tools = expected.get("tool_keys") or []
    if exp_plan.get("calls_contain"):
        if not all(t in actual for t in exp_plan["calls_contain"]):
            return False
    elif len(actual) != len(exp_tools) or actual != exp_tools:
        return False
    # 两日诊断：funnel_daily 必须 days=2，不得用 days=9
    fd_days = exp_plan.get("funnel_daily_days_must_be")
    if fd_days is not None:
        found = False
        for c in calls:
            if (c.get("tool_key") or c.get("tool")) == "funnel_daily":
                if c.get("params", {}).get("days") != fd_days:
                    return False
                found = True
                break
        if not found:
            return False
    return True


def _dt_correct(slots: dict, plan: dict, expected: dict) -> bool | None:
    """dt 是否正确。若该题不需要 dt 返回 None（不计入）。"""
    intent = expected.get("intent", "")
    params = expected.get("params") or []
    first_param = params[0] if params and isinstance(params[0], dict) else {}
    need_dt = intent in INTENTS_NEED_DT or (
        intent == "diagnose_generic" and first_param.get("dt")
    )
    if not need_dt:
        return None

    exp_dt = None
    if first_param:
        exp = first_param.get("dt")
        if exp == "exists":
            exp_dt = "any"
        elif isinstance(exp, str) and exp:
            exp_dt = exp

    actual_dt = plan.get("calls") or []
    actual_dt = actual_dt[0].get("params", {}).get("dt") if actual_dt else None

    if exp_dt == "any":
        return bool(actual_dt)
    if exp_dt is None:
        return True
    return actual_dt == exp_dt


def _boundary_fallback_correct(case: dict, slots: dict, plan: dict) -> bool:
    """边界兜底是否正确。"""
    expected = case.get("expected_slots", {})
    exp_intent = case.get("intent", "")
    act_intent = slots.get("intent", "")

    if case.get("id") == "B03":
        # days 超限应 clamp 至 90
        calls = plan.get("calls") or []
        if not calls:
            return False
        days = calls[0].get("params", {}).get("days")
        return days == 90

    # 其余：intent 正确且 plan 有有效 calls（非失败）
    if exp_intent == "unknown":
        return act_intent == "unknown"
    if case.get("expected_plan", {}).get("not_supported"):
        return bool(plan.get("not_supported"))
    return act_intent == exp_intent and bool(plan.get("calls"))


def _extract_numbers(text: str) -> set[str]:
    """从文本提取数字（整数、小数、百分比）。"""
    if not text:
        return set()
    nums = set()
    for m in re.finditer(r"\d+\.?\d*|\.\d+", text):
        nums.add(m.group(0))
    return nums


def _allowed_numbers_from_answer_obj(obj: dict) -> set:
    """answer_obj 中允许出现的数字。"""
    allowed = set()
    for e in obj.get("evidence", []) or []:
        for k in ("value", "change"):
            v = e.get(k)
            if v is not None:
                allowed.update(_extract_numbers(str(v)))
    for i in obj.get("insights", []) or []:
        if isinstance(i, dict):
            for k in ("text", "value", "change_pct", "delta"):
                v = i.get(k)
                if v is not None:
                    allowed.update(_extract_numbers(str(v)))
    for s in (
        str(obj.get("headline", "")),
        *(obj.get("limitations") or []),
        *(obj.get("assumptions") or []),
    ):
        if s:
            allowed.update(_extract_numbers(str(s)))
    return allowed


def _count_hallucinations(text: str, answer_obj: dict) -> int:
    """统计输出中不存在于 answer_obj 的数字出现次数。"""
    allowed = _allowed_numbers_from_answer_obj(answer_obj)
    out_nums = _extract_numbers(text)
    # 忽略常见无害数字：1,2,3...（如 "第一步"）
    harmless = {str(i) for i in range(0, 10)}
    count = 0
    for n in out_nums:
        if n in harmless:
            continue
        if n not in allowed:
            count += 1
    return count


def _extract_plan_summary(plan: dict) -> dict:
    """提取 plan 摘要供失败记录。"""
    calls = plan.get("calls") or []
    return {
        "calls": [{"tool_key": c.get("tool_key") or c.get("tool"), "params": c.get("params", {})} for c in calls],
        "plots": plan.get("plots", []),
        "not_supported": plan.get("not_supported"),
    }


def _normalize_from_call(fc: str | None) -> str | None:
    """与 narrator 一致：'call_0' -> '0'"""
    if fc is None:
        return None
    s = str(fc).strip()
    if not s:
        return None
    return s[5:] if s.startswith("call_") else s


def _plot_structure_ok(case: dict, plan: dict) -> tuple[bool, str]:
    """
    图表结构断言：
    - overview_daily/funnel_daily/user_retention/user_activity → plan.plots 含 1 个 trend，from_call 指向存在 call
    - category_contrib_buyers → plan.plots 含 topn_bar
    """
    tool_keys = case.get("tool_keys") or []
    exp_plan = case.get("expected_plan") or {}
    if exp_plan.get("calls_contain"):
        tool_keys = list(tool_keys) + list(exp_plan["calls_contain"])
    calls = plan.get("calls") or []
    plots = plan.get("plots") or []
    call_indices = {str(i) for i in range(len(calls))}

    need_trend = any(t in TOOLS_NEED_TREND for t in tool_keys)
    need_topn = "category_contrib_buyers" in tool_keys

    if need_trend:
        trends = [p for p in plots if p.get("plot_type") == "trend"]
        if not trends:
            return False, "需 trend 图但 plan.plots 无 trend"
        for t in trends:
            fc = _normalize_from_call(t.get("from_call"))
            if fc not in call_indices:
                return False, f"trend 的 from_call={t.get('from_call')} 指向不存在的 call（calls 共 {len(calls)} 个）"

    if need_topn:
        topns = [p for p in plots if p.get("plot_type") == "topn_bar"]
        if not topns:
            return False, "需 topn_bar 图但 plan.plots 无 topn_bar"
        for t in topns:
            fc = _normalize_from_call(t.get("from_call"))
            if fc not in call_indices:
                return False, f"topn_bar 的 from_call={t.get('from_call')} 指向不存在的 call"

    return True, ""


def _format_plot_failure_detail(question: str, plan: dict, results: dict | None) -> str:
    """失败时打印：question、plan.calls、plan.plots、from_call df.shape"""
    lines = [
        f"  question: {question}",
        f"  plan.calls: {plan.get('calls', [])}",
        f"  plan.plots: {plan.get('plots', [])}",
    ]
    if results is not None:
        for i, p in enumerate(plan.get("plots") or []):
            fc = _normalize_from_call(p.get("from_call"))
            r = results.get(fc) if fc else None
            df = r.get("df") if r else None
            shape = getattr(df, "shape", "N/A") if df is not None else "None/empty"
            lines.append(f"  plot[{i}] from_call={p.get('from_call')} df.shape={shape}")
    else:
        lines.append("  results: N/A (tools 未执行)")
    return "\n".join(lines)


def run_eval(
    cases_path: str | Path | None = None,
    run_full_answer: bool = False,
    failures_out_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    运行评估，返回指标与失败案例。
    run_full_answer=True 时计算幻觉率（需调 orchestrator，可能失败）。
    failures_out_path 不为 None 时保存失败详情到文件。
    """
    if failures_out_path is None:
        failures_out_path = Path(__file__).resolve().parent / "eval_failures.json"
    cases = _load_cases(cases_path)
    by_id = {c["id"]: c for c in cases}

    intent_pool = [c for c in cases if c["id"] in INTENT_POOL_IDS]

    intent_correct = 0
    template_correct = 0
    dt_need_count = 0
    dt_correct_count = 0
    plot_correct = 0
    plot_total = 0
    boundary_correct = 0
    hallucination_total = 0

    failures: list[dict] = []  # {type, id, question, expected, actual}

    for c in intent_pool:
        q = c.get("question", "")
        slots = map_query(q)
        plan = plan_from_slots(q, slots)

        exp_intent = c.get("intent", "")
        act_intent = slots.get("intent", "")
        if act_intent == exp_intent:
            intent_correct += 1
        else:
            failures.append({
                "type": FAIL_INTENT,
                "id": c.get("id", ""),
                "question": q,
                "expected": {"intent": exp_intent},
                "actual": {"intent": act_intent, "slots": slots},
            })

        if _plan_template_match(plan, c):
            template_correct += 1
        else:
            exp_plan = c.get("expected_plan") or {}
            exp_tools = c.get("tool_keys") or exp_plan.get("calls_contain", [])
            actual_calls = [x.get("tool_key") or x.get("tool") for x in (plan.get("calls") or [])]
            failures.append({
                "type": FAIL_TEMPLATE,
                "id": c.get("id", ""),
                "question": q,
                "expected": {"tool_keys": exp_tools, "calls_contain": exp_plan.get("calls_contain")},
                "actual": {"calls": actual_calls, "plan": _extract_plan_summary(plan)},
            })

        dt_ok = _dt_correct(slots, plan, c)
        if dt_ok is not None:
            dt_need_count += 1
            if dt_ok:
                dt_correct_count += 1
            else:
                params = c.get("params") or []
                exp_dt = params[0].get("dt") if params and isinstance(params[0], dict) else None
                act_dt = (plan.get("calls") or [{}])[0].get("params", {}).get("dt") if plan.get("calls") else None
                failures.append({
                    "type": FAIL_PARAM,
                    "id": c.get("id", ""),
                    "question": q,
                    "expected": {"dt": exp_dt},
                    "actual": {"dt": act_dt, "slots_dt": slots.get("dt")},
                })

        # 图表断言：trend/topn_bar、from_call 有效；df 空时必须记录 limitations，禁止 demo 图
        plot_ok, plot_msg = _plot_structure_ok(c, plan)
        results_for_detail = None
        if run_full_answer:
            try:
                from agent.orchestrator import run_tools
                calls = plan.get("calls") or []
                if calls and not plan.get("not_supported"):
                    results_for_detail = run_tools(calls)
            except Exception:
                pass
        if not plot_ok:
            failures.append({
                "type": FAIL_PLOT,
                "id": c.get("id", ""),
                "question": q,
                "expected": {"plot_rule": plot_msg},
                "actual": {"plan": _extract_plan_summary(plan), "msg": plot_msg},
                "_detail": _format_plot_failure_detail(q, plan, results_for_detail),
            })
        elif results_for_detail is not None and plan.get("plots"):
            from narrator import render_plots
            charts, plot_limitations = render_plots(plan, results_for_detail)
            for idx, p in enumerate(plan.get("plots") or []):
                fc = _normalize_from_call(p.get("from_call"))
                r = results_for_detail.get(fc) if fc else None
                df = r.get("df") if r else None
                is_empty = df is None or (hasattr(df, "empty") and df.empty)
                if is_empty:
                    expected_lim = f"plot[{idx}]" in str(plot_limitations) or f"from_call={fc}" in str(plot_limitations)
                    if not expected_lim:
                        failures.append({
                            "type": FAIL_PLOT,
                            "id": c.get("id", ""),
                            "question": q,
                            "expected": "df 为空时必须记录 limitations，禁止 demo 图",
                            "actual": {"plot_limitations": plot_limitations, "charts_count": len(charts)},
                            "_detail": _format_plot_failure_detail(q, plan, results_for_detail),
                        })
                        break

        if plot_ok:
            plot_correct += 1
        plot_total += 1

    for cid in BOUNDARY_FALLBACK_IDS:
        c = by_id.get(cid)
        if not c:
            continue
        q = c.get("question", "")
        slots = map_query(q)
        plan = plan_from_slots(q, slots)
        if _boundary_fallback_correct(c, slots, plan):
            boundary_correct += 1
        else:
            failures.append({
                "type": FAIL_BOUNDARY,
                "id": cid,
                "question": q,
                "expected": {"intent": c.get("intent"), "note": c.get("note", "")},
                "actual": {"intent": slots.get("intent"), "plan": _extract_plan_summary(plan)},
            })

    not_supported_correct = 0
    not_supported_total = 0
    for cid in NOT_SUPPORTED_IDS:
        c = by_id.get(cid)
        if not c:
            continue
        not_supported_total += 1
        q = c.get("question", "")
        slots = map_query(q)
        plan = plan_from_slots(q, slots)
        if plan.get("not_supported") and not (plan.get("calls") or []):
            not_supported_correct += 1
        else:
            failures.append({
                "type": FAIL_NOT_SUPPORTED,
                "id": cid,
                "question": q,
                "expected": {"not_supported": True, "calls": []},
                "actual": {"not_supported": plan.get("not_supported"), "calls": (plan.get("calls") or [])},
            })

    if run_full_answer:
        try:
            from agent.orchestrator import answer
            for c in intent_pool:
                q = c.get("question", "").strip()
                if not q:
                    continue
                try:
                    out = answer(q, return_answer_obj=True)
                    text = out[0]
                    answer_obj = out[2] if len(out) >= 3 else None
                    if not answer_obj or not text:
                        continue
                    if answer_obj.get("tool_key") == "not_supported":
                        continue
                    exp_ao = c.get("expected_answer_obj") or {}
                    if exp_ao.get("evidence_sources"):
                        act_src = {e.get("source") for e in (answer_obj.get("evidence") or [])}
                        for es in exp_ao["evidence_sources"]:
                            if es not in act_src:
                                failures.append({
                                    "type": "answer_obj",
                                    "id": c.get("id"),
                                    "question": q,
                                    "expected": {"evidence_sources": exp_ao["evidence_sources"]},
                                    "actual": {"evidence_sources": list(act_src)},
                                })
                                break
                    if exp_ao.get("headline_contains") and exp_ao["headline_contains"] not in (answer_obj.get("headline") or ""):
                        failures.append({
                            "type": "answer_obj",
                            "id": c.get("id"),
                            "question": q,
                            "expected": {"headline_contains": exp_ao["headline_contains"]},
                            "actual": {"headline": answer_obj.get("headline")},
                        })
                    n = _count_hallucinations(text, answer_obj)
                    hallucination_total += n
                except Exception:
                    continue
        except ImportError:
            pass

    intent_total = len(intent_pool)
    # 意图识别准确率
    intent_acc = intent_correct / intent_total if intent_total else 0
    # 参数抽取准确率 = 正确 dt 数 / 需要 dt 的题目数
    dt_acc = dt_correct_count / dt_need_count if dt_need_count else 1.0
    # 模板命中率
    template_acc = template_correct / intent_total if intent_total else 0
    # 边界处理正确率 = 正确兜底题目数 / 5
    boundary_acc = boundary_correct / 5 if BOUNDARY_FALLBACK_IDS else 0
    # 幻觉率
    hallucination_rate = hallucination_total / intent_total if intent_total else 0

    # 保存失败案例
    if failures_out_path:
        try:
            with open(failures_out_path, "w", encoding="utf-8") as f:
                json.dump({
                    "failures": failures,
                    "summary": {
                        "intent_fail": sum(1 for x in failures if x["type"] == FAIL_INTENT),
                        "param_fail": sum(1 for x in failures if x["type"] == FAIL_PARAM),
                        "template_fail": sum(1 for x in failures if x["type"] == FAIL_TEMPLATE),
                        "plot_fail": sum(1 for x in failures if x["type"] == FAIL_PLOT),
                        "boundary_fail": sum(1 for x in failures if x["type"] == FAIL_BOUNDARY),
                        "not_supported_fail": sum(1 for x in failures if x["type"] == FAIL_NOT_SUPPORTED),
                        "hallucination_count": hallucination_total,
                    },
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return {
        "intent_accuracy": intent_acc,
        "intent_correct": intent_correct,
        "intent_total": intent_total,
        "param_accuracy": dt_acc,
        "dt_correct": dt_correct_count,
        "dt_need_total": dt_need_count,
        "template_accuracy": template_acc,
        "template_correct": template_correct,
        "template_total": intent_total,
        "plot_accuracy": plot_correct / plot_total if plot_total else 1.0,
        "plot_correct": plot_correct,
        "plot_total": plot_total,
        "boundary_accuracy": boundary_acc,
        "boundary_correct": boundary_correct,
        "boundary_total": 5,
        "not_supported_accuracy": not_supported_correct / not_supported_total if not_supported_total else 1.0,
        "not_supported_correct": not_supported_correct,
        "not_supported_total": not_supported_total,
        "hallucination_rate": hallucination_rate,
        "hallucination_count": hallucination_total,
        "failures": failures,
    }


def _print_failure_report(metrics: dict) -> None:
    """打印失败案例报告。"""
    failures = metrics.get("failures") or []
    if not failures:
        print("无失败案例。\n")
        return

    total_checks = metrics.get("intent_total", 30) + metrics.get("template_total", 30) + metrics.get("plot_total", 30) + 5
    fail_count = len(failures)
    fail_rate = fail_count / total_checks if total_checks else 0

    by_type: dict[str, list] = {}
    for f in failures:
        t = f.get("type", "其他")
        by_type.setdefault(t, []).append(f)

    print("=== 失败案例报告 ===\n")
    print(f"失败率 = {fail_count} 次失败 / {total_checks} 次检查 ≈ {fail_rate:.1%}\n")
    print("各类失败数量：")
    for t, lst in sorted(by_type.items()):
        print(f"  {t}: {len(lst)}")
    print()

    print("示例案例（每类取 1 条）：")
    for t in [FAIL_INTENT, FAIL_PARAM, FAIL_TEMPLATE, FAIL_PLOT, FAIL_BOUNDARY, FAIL_NOT_SUPPORTED]:
        lst = by_type.get(t, [])
        if not lst:
            continue
        f = lst[0]
        q = str(f.get("question", ""))[:50]
        if len(str(f.get("question", ""))) > 50:
            q += "..."
        print(f"\n  [{t}] id={f.get('id')}")
        print(f"    输入：{q}")
        print(f"    期望：{f.get('expected')}")
        act = f.get("actual", {})
        act_str = json.dumps(act, ensure_ascii=False)
        if len(act_str) > 150:
            act_str = act_str[:150] + "..."
        print(f"    实际：{act_str}")
        detail = f.get("_detail")
        if detail:
            print("    详情：")
            print(detail)
    print()


def main():
    import sys
    run_full = "--full" in sys.argv
    out_path = Path(__file__).resolve().parent / "eval_failures.json" if "--no-save" not in sys.argv else None
    metrics = run_eval(run_full_answer=run_full, failures_out_path=out_path)
    print("=== 评估指标 ===\n")
    print(f"意图识别准确率 = {metrics['intent_correct']}/{metrics['intent_total']} = {metrics['intent_accuracy']:.2%}")
    print(f"参数抽取准确率 = {metrics['dt_correct']}/{metrics['dt_need_total']} = {metrics['param_accuracy']:.2%}")
    print(f"模板命中率     = {metrics['template_correct']}/{metrics['template_total']} = {metrics['template_accuracy']:.2%}")
    print(f"图表断言正确率 = {metrics['plot_correct']}/{metrics['plot_total']} = {metrics['plot_accuracy']:.2%}")
    print(f"边界处理正确率 = {metrics['boundary_correct']}/{metrics['boundary_total']} = {metrics['boundary_accuracy']:.2%}")
    print(f"暂时无法查询正确率 = {metrics['not_supported_correct']}/{metrics['not_supported_total']} = {metrics['not_supported_accuracy']:.2%}")
    print(f"幻觉率         = {metrics['hallucination_count']}/{metrics['template_total']} = {metrics['hallucination_rate']:.2%}")
    if not run_full:
        print("\n（幻觉率需 --full 跑完整 answer 计算，依赖 DB）")
    if out_path:
        print(f"\n失败案例已保存至 {out_path}")
    _print_failure_report(metrics)


if __name__ == "__main__":
    main()
