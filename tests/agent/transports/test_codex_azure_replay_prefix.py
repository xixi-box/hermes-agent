"""Azure replay must preserve successful request prefixes (#155671)."""

from copy import deepcopy

import pytest

from agent.transports.codex import ResponsesApiTransport


AZURE_URLS = [
    "https://placeholder.openai.azure.com/openai/v1",
    "https://placeholder.services.ai.azure.com/api/projects/placeholder/openai/v1",
]


def answer(text):
    return {
        "role": "assistant", "content": text,
        "codex_reasoning_items": [
            {"type": "reasoning", "id": f"rs_{text}", "encrypted_content": f"sealed-{text}", "summary": []},
        ],
        "codex_message_items": [{
            "type": "message", "role": "assistant", "id": f"msg_{text}", "status": "completed",
            "content": [{"type": "output_text", "text": text}],
        }],
    }


def build(transport, messages, base_url, **params):
    return transport.build_kwargs(
        model="gpt-6.1-sol", messages=messages, base_url=base_url,
        session_id=params.pop("session_id", "azure-conversation"), **params,
    )["input"]


def reasoning(items):
    return [item["encrypted_content"] for item in items if item.get("type") == "reasoning"]


@pytest.mark.parametrize("base_url", AZURE_URLS)
def test_azure_append_preserves_replayed_prefix(base_url):
    transport = ResponsesApiTransport()
    messages = [{"role": "user", "content": "first"}, answer("one"), {"role": "user", "content": "second"}]
    first = build(transport, messages, base_url)
    messages += [answer("two"), {"role": "user", "content": "third"}]
    canonical = deepcopy(messages)
    second = build(transport, messages, base_url)
    assert second[:len(first)] == first, "Azure replay rewrote an already-cached prefix"
    assert reasoning(second) == ["sealed-one"]
    assert all("id" not in item for item in second if item.get("role") == "assistant")
    assert messages == canonical


@pytest.mark.parametrize("suppression", ["post-tool", "disabled"])
def test_azure_suppression_retires_old_reasoning(suppression):
    base_url = AZURE_URLS[1]
    transport = ResponsesApiTransport()
    messages = [{"role": "user", "content": "first"}, answer("one"), {"role": "user", "content": "second"}]
    assert reasoning(build(transport, messages, base_url)) == ["sealed-one"]
    params = {}
    if suppression == "post-tool":
        tool_turn = answer("tool")
        tool_turn["tool_calls"] = [{"id": "call_x", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]
        messages += [tool_turn, {"role": "tool", "tool_call_id": "call_x", "content": "done"}]
    else:
        params["replay_encrypted_reasoning"] = False
    suppressed = build(transport, messages, base_url, **params)
    assert not reasoning(suppressed)
    messages += [{"role": "assistant", "content": "done"}, {"role": "user", "content": "continue"}]
    continued = build(transport, messages, base_url)
    assert not reasoning(continued)
    assert continued[:len(suppressed)] == suppressed
    messages += [answer("two"), {"role": "user", "content": "third"}]
    first = build(transport, messages, base_url)
    assert first[:len(continued)] == continued
    assert reasoning(first) == ["sealed-two"]
    messages += [answer("three"), {"role": "user", "content": "fourth"}]
    second = build(transport, messages, base_url)
    assert second[:len(first)] == first
    assert reasoning(second) == ["sealed-two"]


def recover(messages):
    from agent.codex_responses_adapter import strip_unverified_reasoning_items

    assert strip_unverified_reasoning_items(messages, issuer_kind="other:verified", issuer_model="gpt-6.1-sol") == 1
    return messages


def compress(messages):
    return [{"role": "user", "content": "summary"}]


def checkpoint(messages):
    return messages + [{"role": "assistant", "content": "", "codex_reasoning_items": [
        {"type": "compaction", "encrypted_content": "checkpoint"},
    ]}]


@pytest.mark.parametrize("rewrite,params,new_transport,base_url", [
    pytest.param(recover, {}, False, AZURE_URLS[1], id="recovery"),
    pytest.param(compress, {}, False, AZURE_URLS[1], id="compression"),
    pytest.param(checkpoint, {"context_management": [{"type": "compaction", "compact_threshold": 1000}]}, False, AZURE_URLS[1], id="checkpoint"),
    pytest.param(list, {}, False, AZURE_URLS[0], id="endpoint"),
    pytest.param(list, {"request_overrides": {"model": "gpt-5.4"}}, False, AZURE_URLS[1], id="model"),
    pytest.param(list, {"session_id": "new-session"}, False, AZURE_URLS[1], id="session"),
    pytest.param(list, {}, True, AZURE_URLS[1], id="resume"),
])
def test_azure_replay_epoch_preserves_the_new_prefix(rewrite, params, new_transport, base_url):
    transport = ResponsesApiTransport()
    messages = [{"role": "user", "content": "first"}, answer("one"), {"role": "user", "content": "second"}]
    assert reasoning(build(transport, messages, AZURE_URLS[1])) == ["sealed-one"]
    messages = rewrite(messages)
    if new_transport:
        transport = ResponsesApiTransport()
    messages += [answer("two"), {"role": "user", "content": "third"}]
    first = build(transport, messages, base_url, **params)
    assert reasoning(first) == ["sealed-two"]
    messages += [answer("three"), {"role": "user", "content": "fourth"}]
    second = build(transport, messages, base_url, **params)
    assert second[:len(first)] == first
    assert reasoning(second) == ["sealed-two"]


def test_azure_anchor_skips_foreign_reasoning():
    transport = ResponsesApiTransport()
    messages = [{"role": "user", "content": "first"}, answer("one"), {"role": "user", "content": "second"}, answer("foreign")]
    messages[-1]["codex_reasoning_items"][0]["_issuer_kind"] = "other:https://foreign.example/v1"
    messages += [{"role": "user", "content": "third"}]
    first = build(transport, messages, AZURE_URLS[0])
    assert reasoning(first) == ["sealed-one"]
    messages += [answer("two"), {"role": "user", "content": "fourth"}]
    second = build(transport, messages, AZURE_URLS[0])
    assert second[:len(first)] == first
    assert reasoning(second) == ["sealed-one"]
