PASS_RATE_DECIMALS = 3


def round_ratio(
    value: float | None, decimals: int = PASS_RATE_DECIMALS
) -> float | None:
    """Round a 0..1 ratio to the project's standard precision.

    Default 3 decimals on the ratio = 1 decimal when rendered as a percent
    (`0.875` → `"87.5%"`). `None` passes through so empty-state handling
    (no completed runs with results) stays unchanged.
    """
    if value is None:
        return None
    return round(value, decimals)


def pass_rate(passed: int, total: int) -> float | None:
    """Ratio of passed / total in [0, 1]; None when total is 0.

    Raw (unrounded) — callers that expose this on a response field must wrap
    in `round_ratio`. The raw form is required for aggregation: rounding
    before averaging causes drift (mean of [round(1/3), 1.0] = 0.666 vs
    round(mean([1/3, 1.0])) = 0.667). See plan 044.
    """
    if total <= 0:
        return None
    return passed / total
