"""Pure Anthropic Messages wire-format helpers. Databricks' `/v1/messages`
channel is otherwise a byte-for-byte passthrough of the native Anthropic SSE
(see `transport.browser` + `domain.ports.PassthroughAccumulator`), so the only
shaping needed on this side is the error envelope."""


def error_response(message: str, error_type: str = "api_error") -> dict:
    return {"type": "error", "error": {"type": error_type, "message": message}}


def unavailable_error(message: str = "session initializing") -> dict:
    return error_response(message, "overloaded_error")
