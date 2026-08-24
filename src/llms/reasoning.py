"""
Unified reasoning effort mapper.

Maps abstract levels ("low", "medium", "high", "xhigh") to provider-specific
parameters. Detection-based: checks which reasoning keys exist in the model's
parameters/extra_body from models.json, then adjusts values for those specific keys.

"xhigh" is only natively honored by Anthropic adaptive thinking (Opus 4.7+)
and GLM 5.2+ (mapped to "max"); for all other providers it is clamped to "high".
"""

REASONING_LEVELS = ("low", "medium", "high", "xhigh")

# Anthropic thinking budgets per level
_ANTHROPIC_BUDGETS = {"low": 5000, "medium": 10000, "high": 32000}

# Gemini numeric thinking budgets per level (for thinking_budget pattern)
_GEMINI_BUDGETS = {"low": 1024, "medium": 8192, "high": 32768}

# GLM 5.2+ native effort levels. "low" skips thinking entirely (consistent with
# the thinking.type pattern); the server collapses "medium" to "high".
_GLM_EFFORT = {"low": "none", "medium": "medium", "high": "high", "xhigh": "max"}


def _clamp(level: str) -> str:
    """Clamp xhigh down to high for providers that don't support xhigh."""
    return "high" if level == "xhigh" else level


def apply_reasoning_effort(
    level: str,
    parameters: dict,
    extra_body: dict,
) -> tuple[dict, dict]:
    """Apply reasoning effort override to model parameters.

    Detects which reasoning pattern the model uses by checking existing keys
    in parameters and extra_body, then adjusts their values.

    Args:
        level: One of "low", "medium", "high", "xhigh". "xhigh" only affects
            Anthropic adaptive thinking; elsewhere it is clamped to "high".
        parameters: Model parameters dict (will be mutated).
        extra_body: Extra body dict (will be mutated).

    Returns:
        Tuple of (parameters, extra_body) — same objects, mutated in place.
    """
    if level not in REASONING_LEVELS:
        return parameters, extra_body

    # --- parameters-based patterns ---

    # OpenAI: parameters.reasoning.effort
    if "reasoning" in parameters:
        if isinstance(parameters["reasoning"], dict):
            parameters["reasoning"]["effort"] = _clamp(level)
        else:
            parameters["reasoning"] = {"effort": _clamp(level)}

    # Anthropic adaptive: control via output_config.effort (supports xhigh
    # natively). Gated on the output_config key so MiniMax-M3 — which also
    # uses thinking.type=adaptive, but only accepts adaptive|disabled —
    # doesn't get a Claude budget/effort payload.
    elif "output_config" in parameters:
        parameters.setdefault("output_config", {})["effort"] = level

    # MiniMax-M3 (Anthropic-compat and OpenAI-compat): official enum is
    # adaptive|disabled. "enabled" and budget_tokens are Claude vocabulary
    # and are rejected or ignored. low → off; anything else → on.
    elif (
        "thinking" in parameters
        and isinstance(parameters["thinking"], dict)
        and parameters["thinking"].get("type") in ("adaptive", "disabled")
    ):
        parameters["thinking"]["type"] = (
            "disabled" if level == "low" else "adaptive"
        )
        parameters["thinking"].pop("budget_tokens", None)

    # Anthropic enabled: control via budget_tokens
    elif "thinking" in parameters:
        budget = _ANTHROPIC_BUDGETS[_clamp(level)]
        if isinstance(parameters["thinking"], dict):
            parameters["thinking"]["budget_tokens"] = budget
        else:
            parameters["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }

    # Gemini 3.x: parameters.thinking_level
    elif "thinking_level" in parameters:
        parameters["thinking_level"] = _clamp(level)

    # Gemini 2.x: parameters.thinking_budget (numeric)
    elif "thinking_budget" in parameters:
        parameters["thinking_budget"] = _GEMINI_BUDGETS[_clamp(level)]

    # vLLM / Groq / Cerebras: parameters.reasoning_effort
    elif "reasoning_effort" in parameters:
        parameters["reasoning_effort"] = _clamp(level)

    # --- extra_body patterns ---

    # extra_body.thinking.type — two vocabularies share this key:
    # MiniMax-M3 (CN OpenAI) ships type=adaptive; Doubao/GLM ship type=enabled.
    # Detect off the current type so a disabled→on flip keeps the vendor's on-word.
    if "thinking" in extra_body:
        current = (
            extra_body["thinking"].get("type")
            if isinstance(extra_body["thinking"], dict)
            else None
        )
        on_type = "adaptive" if current == "adaptive" else "enabled"
        if isinstance(extra_body["thinking"], dict):
            extra_body["thinking"]["type"] = (
                "disabled" if level == "low" else on_type
            )
        else:
            extra_body["thinking"] = {
                "type": "disabled" if level == "low" else on_type
            }

    # Dashscope / Qwen: extra_body.enable_thinking
    if "enable_thinking" in extra_body:
        extra_body["enable_thinking"] = level != "low"

    # GLM 5.2+: extra_body.reasoning_effort (merged top-level into the request body)
    if "reasoning_effort" in extra_body:
        extra_body["reasoning_effort"] = _GLM_EFFORT[level]

    return parameters, extra_body
