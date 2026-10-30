"""Exercise real LiteLLM function dispatch with offline HTTP, without MCP."""

import asyncio
import importlib.abc
import sys

import pytest


def test_adk_openai_function_tools_do_not_import_mcp(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("OPENAI_API_KEY", "offline-not-a-key")
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    pytest.importorskip("google.adk.models.lite_llm")
    litellm = pytest.importorskip("litellm")
    from src.adapters.llm.adk_provider import build_adk_model
    from src.config import Settings

    settings = Settings(openai_api_key="offline-not-a-key", openai_model="gpt-4o-mini")
    model = build_adk_model("openai", settings)
    assert model._additional_args["_skip_mcp_handler"] is True

    class RejectMcp(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "mcp" or fullname.startswith(("mcp.", "litellm.responses.mcp.")):
                raise AssertionError(f"Unexpected MCP import: {fullname}")
            return None

    monkeypatch.setattr(sys, "meta_path", [RejectMcp(), *sys.meta_path])
    received = []

    def respond(request):
        import json

        received.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 0,
            "model": "gpt-4o-mini", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Offline verified"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    tools = [{"type": "function", "function": {
        "name": "damage_calc", "description": "Calculate damage",
        "parameters": {"type": "object", "properties": {}},
    }}]

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = openai.AsyncOpenAI(api_key="offline-not-a-key", http_client=http)
            return await litellm.acompletion(
                model=model.model, messages=[{"role": "user", "content": "Offline"}],
                tools=tools, client=client, **model._additional_args,
            )

    result = asyncio.run(run())
    assert result.choices[0].message.content == "Offline verified"
    assert len(received) == 1
    assert received[0]["tools"] == tools
    assert "_skip_mcp_handler" not in received[0]
