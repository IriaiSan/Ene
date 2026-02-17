"""Model pricing table for cost calculation.

Prices are per million tokens from OpenRouter.
Updated manually when models change or new ones are added.
"""

from __future__ import annotations

from loguru import logger


# Prices per million tokens (USD) — from OpenRouter
# Format: "model_id": {"input": price, "output": price}
MODEL_PRICING: dict[str, dict[str, float]] = {
    # DeepSeek
    "deepseek/deepseek-v3.2": {"input": 0.14, "output": 0.28},
    "deepseek/deepseek-chat-v3-0324": {"input": 0.19, "output": 0.87},
    "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek/deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "deepseek/deepseek-r1": {"input": 0.55, "output": 2.19},
    # OpenAI (via OpenRouter)
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/gpt-4-turbo": {"input": 10.00, "output": 30.00},
    # Anthropic (via OpenRouter)
    "anthropic/claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
    "anthropic/claude-3-haiku": {"input": 0.25, "output": 1.25},
    # Google (via OpenRouter)
    "google/gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "google/gemini-pro-1.5": {"input": 1.25, "output": 5.00},
    # Meta (via OpenRouter)
    "meta-llama/llama-3.1-70b-instruct": {"input": 0.52, "output": 0.75},
    "meta-llama/llama-3.1-8b-instruct": {"input": 0.06, "output": 0.06},
    # Free models (OpenRouter :free tier — updated 2026-02-18)
    "openrouter/auto": {"input": 0.0, "output": 0.0},
    "qwen/qwen3-next-80b-a3b-instruct:free": {"input": 0.0, "output": 0.0},
    "mistralai/mistral-small-3.1-24b-instruct:free": {"input": 0.0, "output": 0.0},
    "nvidia/nemotron-nano-9b-v2:free": {"input": 0.0, "output": 0.0},
    "meta-llama/llama-3.3-70b-instruct:free": {"input": 0.0, "output": 0.0},
    "google/gemma-3-27b-it:free": {"input": 0.0, "output": 0.0},
}

# Fallback pricing for unknown models
DEFAULT_PRICING = {"input": 1.00, "output": 2.00}


def calculate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Calculate USD cost from token counts and model pricing.

    Args:
        model: Model identifier (e.g., "deepseek/deepseek-v3.2").
        prompt_tokens: Number of input/prompt tokens.
        completion_tokens: Number of output/completion tokens.

    Returns:
        Estimated cost in USD.
    """
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)

    if model not in MODEL_PRICING:
        logger.debug(f"No pricing for model '{model}', using default ${DEFAULT_PRICING}")

    input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    output_cost = (completion_tokens / 1_000_000) * pricing["output"]

    return round(input_cost + output_cost, 6)


def get_model_pricing(model: str) -> dict[str, float]:
    """Get pricing for a model, with fallback to default."""
    return MODEL_PRICING.get(model, DEFAULT_PRICING)


def list_known_models() -> list[str]:
    """Return all models with known pricing."""
    return sorted(MODEL_PRICING.keys())
