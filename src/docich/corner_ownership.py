"""Fail-closed runtime generation binding for common rotation executions."""


def bind_runtime(store, state, target):
    if not state.get("rotation_request_id"):
        return
    canonical, _ = store.canonical.load()
    active = canonical.get("active") or {}
    runtime_id = active.get("runtime_id")
    if canonical.get("phase") != "ready" or active.get("game") != target or not runtime_id:
        raise RuntimeError("rotation runtime ownership unverified")
    expected = state.get("rotation_runtime_id")
    if expected is not None and runtime_id != expected:
        raise RuntimeError("rotation runtime generation changed")
    state["rotation_runtime_id"] = runtime_id


def verify_runtime(store, state, target):
    if not state.get("rotation_request_id"):
        return
    if not state.get("rotation_runtime_id"):
        raise RuntimeError("rotation runtime identity missing")
    bind_runtime(store, state, target)
