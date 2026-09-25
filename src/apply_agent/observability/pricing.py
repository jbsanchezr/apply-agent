"""What an LLM call cost, in US dollars.

Prices are Anthropic's first-party list prices per million tokens (checked
2026-06). Output tokens include adaptive-thinking tokens, which are billed as
output. Prompt-cache discounts are not modelled because the agent does not
use prompt caching. Local models cost nothing per call. An unknown hosted
model returns ``None``, never a silent 0.
"""

from typing import Final

_PER_MILLION: Final = 1_000_000

# model id -> (input $/MTok, output $/MTok)
ANTHROPIC_PRICES: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
FREE_PROVIDERS: Final = frozenset({"ollama", "keyword-baseline"})


def cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float | None:
    if provider in FREE_PROVIDERS:
        return 0.0
    prices = ANTHROPIC_PRICES.get(model) if provider == "anthropic" else None
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / _PER_MILLION
