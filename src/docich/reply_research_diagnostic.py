"""Optional research metadata projection. No content, identifiers or log sink."""

ERROR_NAMES = frozenset({
    "APIError", "AuthenticationError", "ProviderAuthError", "ConfigInvalidError", "ConfigJsonError",
    "ProviderModelNotFoundError", "ModelNotFoundError", "UnknownError",
    "ContextOverflowError", "TimeoutError", "NetworkError",
})
TRANSPORT_CODES = frozenset({
    "ECONNREFUSED", "ECONNRESET", "ENOTFOUND", "EAI_AGAIN", "ETIMEDOUT",
    "CERT_HAS_EXPIRED", "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
})
STAGES = frozenset({
    "cli_spawn", "cli_running", "cli_stdout", "cli_exit", "cli_failure",
    "cli_reaped", "cli_error_event", "model_call", "model_proposal",
})
REASONS = frozenset({
    "invalid_json", "timeout", "output_limit", "provider_failed", "spawn_failed",
    "unexpected_event", "unexpected_second_generation", "unexpected_finish_or_cost",
    "output_tokens_exceeded", "unclassified_failure", "invalid_proposal",
})
CATEGORIES = frozenset({
    "model_auth", "rate_limit", "network_tls", "cli_config_or_model",
    "timeout", "provider_http", "unclassified_error",
})


def allowed_row(row):
    """Project only bounded integers and exact enums; ignore forged fields."""
    if type(row) is not dict:
        return None
    stage = row.get("stage")
    if type(stage) is not str or stage not in STAGES:
        return None
    out = {"stage": stage}
    for key, low, high in (("elapsed_ms", 0, 100000), ("returncode", -128, 255),
                           ("http_status", 100, 599)):
        value = row.get(key)
        if type(value) is int and low <= value <= high:
            out[key] = value
    for key, choices in (("reason", REASONS), ("category", CATEGORIES),
                         ("error_name", ERROR_NAMES | {"unrecognized"}),
                         ("transport_code", TRANSPORT_CODES)):
        value = row.get(key)
        if type(value) is str and value in choices:
            out[key] = value
    return out


def emit(sink, row):
    """A broken optional sink must never prevent process cleanup or alter routing."""
    if sink is None:
        return
    safe = allowed_row(row)
    if safe is not None:
        try:
            sink(safe)
        except Exception:
            pass


def structured_error(event):
    """Provider error metadata only: never message, body, headers or paths."""
    if type(event) is not dict or event.get("type") != "error":
        return None
    error = event.get("error")
    error = error if type(error) is dict else {}
    data = error.get("data")
    data = data if type(data) is dict else {}
    row = allowed_row({"stage": "cli_error_event", "error_name": error.get("name"),
                       "http_status": data.get("statusCode"), "transport_code": data.get("code")})
    name = row.setdefault("error_name", "unrecognized")
    status = row.get("http_status")
    if status in (401, 403) or name in {"AuthenticationError", "ProviderAuthError"}:
        category = "model_auth"
    elif status == 429:
        category = "rate_limit"
    elif row.get("transport_code") or name == "NetworkError":
        category = "network_tls"
    elif name in {"ConfigInvalidError", "ConfigJsonError", "ModelNotFoundError", "ProviderModelNotFoundError"}:
        category = "cli_config_or_model"
    elif name == "TimeoutError":
        category = "timeout"
    elif status:
        category = "provider_http"
    else:
        category = "unclassified_error"
    row["category"] = category
    return row
