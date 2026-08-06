from __future__ import annotations

import logging

import pytest

from fusion_science.config import ScienceConfig
from fusion_science.core.agent import ScienceAgent
from fusion_science.core.engine import ScienceEngine
from fusion_science.core.gateway import LLMGateway

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration]

API_KEY = "dahai168"
BASE_URL = "http://localhost:11434/v1"
MODEL = "Qwen3.5-4B-bf16"


async def _detect_model() -> str:
    import httpx

    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            r = await client.get("/models", headers={"Authorization": f"Bearer {API_KEY}"})
            r.raise_for_status()
            data = r.json().get("data", [])
            text_models = [m["id"] for m in data if m.get("capabilities", {}).get("text_generation", False)]
            for candidate in [MODEL, "qwen3.5-9b", "Qwen3.5-9B-4bit"]:
                if candidate in text_models:
                    logger.info("Using model: %s", candidate)
                    return candidate
            if text_models:
                chosen = text_models[0]
                logger.info("Fallback to first text model: %s", chosen)
                return chosen
    except Exception as e:
        logger.warning("Model detection failed: %s", e)
    return MODEL


@pytest.fixture
async def gateway():
    model = await _detect_model()
    gw = LLMGateway(
        model=model,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=0.1,
        max_tokens=256,
        timeout=60.0,
    )
    healthy = await gw.health()
    if not healthy:
        await gw.close()
        pytest.skip("fusion-mlx service not available")
    yield gw
    await gw.close()


@pytest.fixture
def config():
    return ScienceConfig(
        model_name=MODEL,
        engine_base_url=BASE_URL,
        engine_api_key=API_KEY,
    )


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_gateway_health(gateway):
    logger.info("Testing LLMGateway.health()")
    result = await gateway.health()
    logger.info("Health check result: %s", result)
    assert result is True


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_gateway_chat(gateway):
    logger.info("Testing LLMGateway.chat() simple completion")
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Reply in one short sentence."},
        {"role": "user", "content": "What is 2 + 2?"},
    ]
    resp = await gateway.chat(messages)
    logger.info("Chat response: model=%s, content_len=%d, finish=%s", resp.model, len(resp.content), resp.finish_reason)
    assert len(resp.content) > 0, "LLM returned empty content"
    assert resp.model != ""


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_gateway_chat_stream(gateway):
    logger.info("Testing LLMGateway.chat_stream()")
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Reply in one short sentence."},
        {"role": "user", "content": "What is the capital of France?"},
    ]
    chunks = []
    async for token in gateway.chat_stream(messages):
        chunks.append(token)
    logger.info("Stream collected %d chunks, total len=%d", len(chunks), sum(len(c) for c in chunks))
    assert len(chunks) >= 1, "Expected at least 1 stream chunk"
    full = "".join(chunks)
    assert len(full) > 0, "Stream produced empty output"


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_gateway_structured_output(gateway):
    logger.info("Testing LLMGateway.structured_output()")
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["answer"],
    }
    messages = [
        {"role": "user", "content": "What is the boiling point of water in Celsius?"},
    ]
    result = await gateway.structured_output(messages, schema=schema)
    logger.info(
        "Structured output: error=%s, parsed=%s, raw_len=%d",
        result.error,
        type(result.parsed).__name__ if result.parsed else None,
        len(result.raw),
    )
    assert result.error == "" or result.parsed is not None, f"Structured output failed: {result.error}"
    if result.parsed is not None:
        assert isinstance(result.parsed, dict), "Parsed result should be a dict"
        assert "answer" in result.parsed, "Parsed result missing 'answer' key"


@pytest.mark.timeout(90)
@pytest.mark.asyncio
async def test_science_agent_e2e(gateway):
    logger.info("Testing ScienceAgent end-to-end with real LLM")
    agent = ScienceAgent(
        name="test_agent",
        engine=gateway,
        system_prompt="You are a science assistant. Answer briefly in one sentence.",
    )
    result = await agent.run("What is DNA?", max_iterations=3)
    logger.info(
        "Agent result: name=%s, output_len=%d, steps=%d, duration=%.2fs, error=%s",
        result.agent_name,
        len(result.output),
        len(result.steps),
        result.duration,
        result.error,
    )
    assert result.error == "", f"Agent returned error: {result.error}"
    assert len(result.output) > 0, "Agent produced empty output"
    assert result.agent_name == "test_agent"
    assert len(result.steps) >= 1, "Agent should have at least 1 step"


@pytest.mark.timeout(30)
@pytest.mark.asyncio
async def test_science_engine_backward_compat(config):
    logger.info("Testing ScienceEngine backward compat re-export")
    engine = ScienceEngine(
        model=config.model_name,
        base_url=config.engine_base_url,
        api_key=config.engine_api_key,
        temperature=0.1,
        max_tokens=64,
        timeout=30.0,
    )
    assert ScienceEngine is LLMGateway, "ScienceEngine should be LLMGateway alias"
    assert isinstance(engine, LLMGateway), "ScienceEngine instance should be LLMGateway"
    assert engine.model == config.model_name
    messages = engine.build_science_prompt("Test prompt")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    logger.info("ScienceEngine backward compat verified")
    await engine.close()
