"""Live smoke test of the OpenRouter models the agents use: the key works and the timeout settings hold.

Sends one tiny prompt to each agent model through the real OpenRouter API (a few hundred tokens in
total). The copies only lower the output cap and reasoning effort to keep the test cheap; the
timeout and retry settings under test are the ones in src/models.py.
"""

import time

import pytest

from src.models import REQUEST_TIMEOUT_MS, SDK_MAX_RETRIES, model1, model2, model3, model4

AGENT_MODELS = [model1, model4]  # subagents and supervisor (src/agents.py)


@pytest.mark.parametrize("model", [model1, model2, model3, model4], ids=lambda m: m.model_name)
def test_every_model_has_the_lambda_safe_timeout(model):
    assert model.request_timeout == REQUEST_TIMEOUT_MS == 240_000
    assert model.max_retries == SDK_MAX_RETRIES == 1
    # The value must reach the OpenRouter SDK client, which works in milliseconds.
    assert model.client.sdk_configuration.timeout_ms == REQUEST_TIMEOUT_MS


@pytest.mark.parametrize("model", AGENT_MODELS, ids=lambda m: m.model_name)
def test_agent_model_answers_live(model):
    cheap = model.model_copy(update={"max_tokens": 300, "reasoning": {"effort": "low"}})
    start = time.time()
    reply = cheap.invoke("Reply with the single word: ok")
    elapsed = time.time() - start

    assert reply.text.strip(), f"{model.model_name} returned an empty reply"
    assert elapsed < REQUEST_TIMEOUT_MS / 1000
    print(f"{model.model_name}: {reply.text.strip()[:40]!r} in {elapsed:.1f}s, usage={reply.usage_metadata}")
