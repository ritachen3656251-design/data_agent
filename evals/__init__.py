# evals - evaluation scripts
# Use: python -m evals.run_eval_metrics, python -m evals.run_eval_multiturn, etc.
__all__ = ["run_eval", "run_multiturn_eval"]

def __getattr__(name):
    if name == "run_eval":
        from .run_eval_metrics import run_eval
        return run_eval
    if name == "run_multiturn_eval":
        from .run_eval_multiturn import run_multiturn_eval
        return run_multiturn_eval
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
