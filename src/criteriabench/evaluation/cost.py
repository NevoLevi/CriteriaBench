"""Transparent token-cost estimation."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def calculate_token_cost(
    input_tokens: int,
    output_tokens: int,
    *,
    input_per_million_usd: float,
    output_per_million_usd: float,
) -> float:
    """Estimate request cost and round to micro-dollar precision."""

    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts cannot be negative")
    if input_per_million_usd < 0 or output_per_million_usd < 0:
        raise ValueError("token prices cannot be negative")

    million = Decimal(1_000_000)
    total = (
        Decimal(input_tokens) * Decimal(str(input_per_million_usd)) / million
        + Decimal(output_tokens) * Decimal(str(output_per_million_usd)) / million
    )
    return float(total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
