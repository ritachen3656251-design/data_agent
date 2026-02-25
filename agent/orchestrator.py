# orchestrator.py
# 执行链路：map_query -> plan_from_slots -> [executor] -> narrator
# 成功生成 plan+answer_obj 后写入 session memory

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Callable

import pandas as pd

from mapper import map_query
from planner import plan_from_slots
from narrator import narrate
from tools import run_tool as _run_tool

DEBUG_TRACE = os.environ.get("DEBUG_TRACE", "").lower() in ("1", "true", "yes")

# logs at project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACE_JSONL_PATH = os.path.join(_PROJECT_ROOT, "logs", "trace.jsonl")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _trace_wants_show(question: str) -> bool:
    """用户是否显式要求展示 Trace 摘要。"""
    q = (question or "").strip()
    return q.startswith("/trace") or "show trace" in q.lower() or "debug" in q.lower()


def _df_head_to_dict_list(df: Any, n: int = 2) -> list[dict]:
    """df.head(n) 转 dict list，避免打印整表。"""
    if df is None or not hasattr(df, "head"):
        return []
    try:
        return df.head(n).to_dict(orient="records")
    except Exception:
        return []


def _format_trace_summary(trace_log: dict) -> str:
    """生成 Trace 摘要文本。"""
    lines = ["\n\n--- Trace 摘要 ---", f"trace_id: {trace_log.get('trace_id', '')}"]
    slots = trace_log.get("slots", {})
    lines.append(f"slots: intent={slots.get('intent')} dt={slots.get('dt')} days={slots.get('days')}")
    calls = trace_log.get("plan_calls", [])
    lines.append(f"calls: {[(c.get('tool_key'), c.get('params')) for c in calls]}")
    for i, er in enumerate(trace_log.get("exec_result", [])):
        shape = er.get("df_shape", "N/A")
        err = er.get("error", "")
        lines.append(f"  call[{i}] df.shape={shape} error={err or '-'}")
    lines.append("---")
    return "\n".join(lines)


def _json_safe(obj: Any) -> Any:
    """转为 JSON 可序列化结构：tuple->list，NaN/pd.NA->None。"""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass
    if isinstance(obj, float):
        return None if (obj != obj) else obj  # NaN
    if isinstance(obj, tuple):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    return str(obj)


def _append_trace_to_file(trace_log: dict) -> None:
    """将 trace_log 追加写入 logs/trace.jsonl，一行一个 JSON。"""
    try:
        log_dir = os.path.dirname(TRACE_JSONL_PATH)
        os.makedirs(log_dir, exist_ok=True)
        safe = _json_safe(trace_log)
        line = json.dumps(safe, ensure_ascii=False) + "\n"
        with open(TRACE_JSONL_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # 静默失败，不影响主链路


def _build_answer_summary(answer_obj: dict) -> str:
    """从 answer_obj 提取小摘要：headline + 前 2 条 evidence 文本（不含 df）。"""
    headline = (answer_obj.get("headline") or "").strip()
    evs = answer_obj.get("evidence") or []
    parts = [headline] if headline else []
    for e in evs[:2]:
        if isinstance(e, dict):
            lb = e.get("label", "")
            val = e.get("value", "")
            if lb or val:
                parts.append(f"{lb}: {val}" if lb else str(val))
        elif e:
            parts.append(str(e))
    return " | ".join(p for p in parts if p)


def _save_session_memory(
    session_id: str,
    slots: dict,
    plan: dict,
    answer_obj: dict,
) -> None:
    """成功生成 plan+answer_obj 后写入 session memory。不存 df 原始数据。"""
    try:
        from memory import update_session
    except ImportError:
        return
    patch = {}
    if slots.get("dt"):
        # 连续单日 overview 时保留上一日供「为什么上升/下降」对比
        try:
            from memory import get_session
            prev = get_session(session_id) if session_id else {}
            old_dt = prev.get("last_dt")
            if old_dt and old_dt != slots["dt"] and (prev.get("last_intent") == "overview_day" or "overview_day" in (prev.get("last_tool_keys") or [])):
                patch["prev_dt"] = old_dt
        except Exception:
            pass
        patch["last_dt"] = slots["dt"]
    if slots.get("days") is not None:
        patch["last_days"] = int(slots["days"])
    intent = slots.get("intent")
    calls = plan.get("calls") or []
    tool_keys = [c.get("tool_key") or c.get("tool") for c in calls if c.get("tool_key") or c.get("tool")]
    patch["last_intent"] = intent or (tool_keys[0] if tool_keys else None)
    patch["last_tool_keys"] = tool_keys
    if slots.get("metric_focus"):
        patch["last_metric_focus"] = slots["metric_focus"]
    patch["last_answer_summary"] = _build_answer_summary(answer_obj)
    if patch:
        update_session(session_id, patch)


def _build_exec_result_trace(results: dict[str, dict[str, Any]]) -> list[dict]:
    """从 run_tools 的 results 构建 exec_result 的 trace 记录。"""
    out: list[dict] = []
    for i in range(len(results)):
        r = results.get(str(i), {})
        df = r.get("df")
        shape = getattr(df, "shape", None) if df is not None else "N/A"
        head2 = _df_head_to_dict_list(df, 2) if df is not None else []
        out.append({
            "ok": r.get("ok", False),
            "error": r.get("error") or "",
            "df_shape": shape,
            "df_head_2": head2,
        })
    return out


def run_tools(calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """执行 plan.calls，返回 results dict：key=call_i, value={tool_key, params, ok, error, df}。"""
    results: dict[str, dict[str, Any]] = {}
    for i, c in enumerate(calls):
        tool_key = c.get("tool_key") or c.get("tool", "")
        params = c.get("params") or {}
        try:
            df = _run_tool(tool_key, params)
            ok = df is not None and (not hasattr(df, "empty") or not df.empty)
            results[str(i)] = {
                "tool_key": tool_key,
                "params": params,
                "ok": ok,
                "error": None if ok else "空数据",
                "df": df,
            }
            if DEBUG_TRACE:
                shape = getattr(df, "shape", None) if df is not None else None
                head2 = df.head(2).to_string() if df is not None and hasattr(df, "head") else "N/A"
                print(f"[TRACE] executor call[{i}] {tool_key} params={params} ok={ok} error={'空数据' if not ok else None} df.shape={shape}")
                print(f"[TRACE] executor call[{i}] df.head(2):\n{head2}")
        except Exception as e:
            results[str(i)] = {
                "tool_key": tool_key,
                "params": params,
                "ok": False,
                "error": str(e),
                "df": None,
            }
            if DEBUG_TRACE:
                print(f"[TRACE] executor call[{i}] {tool_key} params={params} ok=False error={e} df.shape=None")
    return results


def answer(
    question: str,
    mapper_fn: Callable[[str], dict] | None = None,
    narrator_fn: Callable[[str, dict, dict], str] | None = None,
    session_ctx: dict | None = None,
    session_id: str | None = None,
    return_answer_obj: bool = False,
) -> tuple[str, list | None] | tuple[str, list | None, dict]:
    """
    answer(question) -> (text, optional_charts)

    执行流程：
    1. slots = map_query(question, session_ctx)
    2. plan = plan_from_slots(question, slots)
    3. 若 plan.not_supported：直接交 narrator
    4. 否则 executor 按 plan.calls 调 run_tool，得到 results
    5. narrator 输出
    """
    mapper = mapper_fn or map_query
    narrate_fn = narrator_fn or narrate

    # Trace 日志：只记录，不改变业务逻辑
    trace_id = new_trace_id()
    trace_log: dict[str, Any] = {
        "trace_id": trace_id,
        "input": {"question": question},
        "slots": {},
        "plan_calls": [],
        "exec_result": [],
        "answer_obj_summary": "",
        "final_preview": "",
    }

    slots = map_query(question, session_ctx) if mapper_fn is None else mapper_fn(question)
    trace_log["slots"] = {
        "intent": slots.get("intent"),
        "dt": slots.get("dt"),
        "days": slots.get("days"),
        "assumptions": slots.get("assumptions"),
        "not_supported": slots.get("not_supported"),
    }

    plan = plan_from_slots(question, slots)
    trace_log["plan_calls"] = [
        {"tool_key": c.get("tool_key") or c.get("tool"), "params": c.get("params") or {}}
        for c in (plan.get("calls") or [])
    ]

    if DEBUG_TRACE:
        print(f"[TRACE] question: {question}")
        print(f"[TRACE] mapper/normalize: intent={slots.get('intent')} dt={slots.get('dt')} days={slots.get('days')} assumptions={slots.get('assumptions')}")
        for i, c in enumerate(plan.get("calls") or []):
            tk = c.get("tool_key") or c.get("tool", "")
            p = c.get("params") or {}
            print(f"[TRACE] plan.calls[{i}]: tool_key={tk} params={p}")

    def _unpack(out):
        if isinstance(out, tuple):
            text = out[0]
            charts = out[1] if len(out) >= 2 else []
            answer_obj = out[2] if len(out) >= 3 else None
            return text, charts, answer_obj
        return out, [], None

    def _finalize_trace(text: str, answer_obj: dict | None, trace: dict) -> str:
        """记录 answer_obj/final 阶段，若用户要求则追加 Trace 摘要，并写入 logs/trace.jsonl。"""
        trace["answer_obj_summary"] = _build_answer_summary(answer_obj or {})
        trace["final_preview"] = (text or "")[:200]
        _append_trace_to_file(trace)
        if _trace_wants_show(question):
            text = (text or "") + _format_trace_summary(trace)
        return text

    # plan.not_supported：直接交 narrator，无 calls
    if plan.get("not_supported"):
        plan_for_narrate = {"calls": [], "plots": [], "not_supported": plan["not_supported"], "assumptions": plan.get("assumptions", [])}
        text, charts, answer_obj = _unpack(narrate_fn(question, plan_for_narrate, {}))
        text = _finalize_trace(text, answer_obj, trace_log)
        if session_id and answer_obj:
            _save_session_memory(session_id, slots, plan_for_narrate, answer_obj)
        return (text, charts, answer_obj) if return_answer_obj else (text, charts)

    calls = plan.get("calls") or []
    if not calls:
        text, charts, answer_obj = _unpack(narrate_fn(question, plan, {}))
        text = _finalize_trace(text, answer_obj, trace_log)
        if session_id and answer_obj:
            _save_session_memory(session_id, slots, plan, answer_obj)
        return (text, charts, answer_obj) if return_answer_obj else (text, charts)

    results = run_tools(calls)
    trace_log["exec_result"] = _build_exec_result_trace(results)
    text, charts, answer_obj = _unpack(narrate_fn(question, plan, results))
    text = _finalize_trace(text, answer_obj, trace_log)
    if session_id and answer_obj:
        _save_session_memory(session_id, slots, plan, answer_obj)
    return (text, charts, answer_obj) if return_answer_obj else (text, charts)
