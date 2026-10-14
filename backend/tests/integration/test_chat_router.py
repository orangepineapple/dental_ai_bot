import pytest
from httpx import AsyncClient

from src.chat.config import chat_settings
from src.chat.constants import FALLBACK_REPLY
from src.chat.dependencies import get_chat_config, get_llm_client
from src.chat.exceptions import LLMUnavailable
from src.chat.llm import LLMResponse
from src.main import app

pytestmark = pytest.mark.integration

PASSWORD = "a-strong-password"


class StubLLM:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.seen: list[list[dict]] = []

    async def complete(self, messages, tools):
        self.seen.append(list(messages))
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(content=self.reply)


async def _auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "first_name": "Jane",
            "last_name": "Doe",
            "birth_date": "1990-04-01",
        },
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def stub_llm():
    llm = StubLLM("Hello from the model")
    app.dependency_overrides[get_llm_client] = lambda: llm
    yield llm
    app.dependency_overrides.pop(get_llm_client, None)


async def test_requires_authentication(client: AsyncClient, stub_llm: StubLLM) -> None:
    resp = await client.post("/chat/messages", json={"message": "hi"})

    assert resp.status_code == 401


async def test_conversation_is_persisted_and_replayed(
    client: AsyncClient, stub_llm: StubLLM
) -> None:
    headers = await _auth_headers(client, "jane@example.com")

    first = await client.post("/chat/messages", json={"message": "hi"}, headers=headers)
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]
    assert first.json()["reply"] == "Hello from the model"

    second = await client.post(
        "/chat/messages",
        json={"message": "book me a cleaning", "conversation_id": conversation_id},
        headers=headers,
    )
    assert second.status_code == 200

    # History was loaded from the DB and sent to the model on turn two.
    contents = [m.get("content") for m in stub_llm.seen[-1]]
    assert "hi" in contents and "Hello from the model" in contents

    transcript = await client.get(f"/chat/conversations/{conversation_id}/messages", headers=headers)
    assert [m["role"] for m in transcript.json()] == ["user", "assistant", "user", "assistant"]


async def test_other_users_conversation_is_not_found(
    client: AsyncClient, stub_llm: StubLLM
) -> None:
    owner = await _auth_headers(client, "owner@example.com")
    other = await _auth_headers(client, "other@example.com")
    created = await client.post("/chat/messages", json={"message": "hi"}, headers=owner)
    conversation_id = created.json()["conversation_id"]

    post = await client.post(
        "/chat/messages",
        json={"message": "hi", "conversation_id": conversation_id},
        headers=other,
    )
    get = await client.get(f"/chat/conversations/{conversation_id}/messages", headers=other)

    assert post.status_code == 404
    assert get.status_code == 404


async def test_provider_outage_returns_degraded_fallback(client: AsyncClient) -> None:
    app.dependency_overrides[get_llm_client] = lambda: StubLLM(LLMUnavailable("down"))
    try:
        headers = await _auth_headers(client, "jane@example.com")
        resp = await client.post("/chat/messages", json={"message": "hi"}, headers=headers)
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    assert resp.status_code == 200
    assert resp.json()["degraded"] is True


async def test_fallback_reply_is_shown_but_not_sent_back_to_model(client: AsyncClient) -> None:
    down = StubLLM(LLMUnavailable("down"))
    app.dependency_overrides[get_llm_client] = lambda: down
    try:
        headers = await _auth_headers(client, "jane@example.com")
        first = await client.post("/chat/messages", json={"message": "hi"}, headers=headers)
        conversation_id = first.json()["conversation_id"]

        recovered = StubLLM("Back online")
        app.dependency_overrides[get_llm_client] = lambda: recovered
        await client.post(
            "/chat/messages",
            json={"message": "are you there", "conversation_id": conversation_id},
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_llm_client, None)

    sent = [m.get("content") for m in recovered.seen[-1]]
    assert FALLBACK_REPLY not in sent
    assert "hi" in sent and "are you there" in sent
    transcript = await client.get(f"/chat/conversations/{conversation_id}/messages", headers=headers)
    assert FALLBACK_REPLY in [m["content"] for m in transcript.json()]


async def test_message_length_is_limited(client: AsyncClient, stub_llm: StubLLM) -> None:
    headers = await _auth_headers(client, "jane@example.com")

    resp = await client.post("/chat/messages", json={"message": "x" * 5000}, headers=headers)

    assert resp.status_code == 422


async def test_conversations_are_listed_for_owner_only(
    client: AsyncClient, stub_llm: StubLLM
) -> None:
    owner = await _auth_headers(client, "owner@example.com")
    other = await _auth_headers(client, "other@example.com")
    first = await client.post("/chat/messages", json={"message": "x" * 100}, headers=owner)
    await client.post("/chat/messages", json={"message": "second chat"}, headers=owner)
    await client.post(
        "/chat/messages",
        json={"message": "more", "conversation_id": first.json()["conversation_id"]},
        headers=owner,
    )

    listed = await client.get("/chat/conversations", headers=owner)
    others = await client.get("/chat/conversations", headers=other)

    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 2
    # Most recently active first: the first chat was continued last.
    assert body[0]["id"] == first.json()["conversation_id"]
    assert body[0]["message_count"] == 4
    assert body[0]["title"] == "x" * 59 + "…"
    assert body[1]["title"] == "second chat"
    assert others.json() == []


async def test_conversations_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/chat/conversations")).status_code == 401


async def test_conversation_message_limit(client: AsyncClient, stub_llm: StubLLM) -> None:
    limited = chat_settings.model_copy(update={"MAX_CONVERSATION_MESSAGES": 4})
    app.dependency_overrides[get_chat_config] = lambda: limited
    try:
        headers = await _auth_headers(client, "jane@example.com")
        first = await client.post("/chat/messages", json={"message": "one"}, headers=headers)
        conversation_id = first.json()["conversation_id"]
        second = await client.post(
            "/chat/messages",
            json={"message": "two", "conversation_id": conversation_id},
            headers=headers,
        )
        third = await client.post(
            "/chat/messages",
            json={"message": "three", "conversation_id": conversation_id},
            headers=headers,
        )
        transcript = await client.get(
            f"/chat/conversations/{conversation_id}/messages", headers=headers
        )
        fresh = await client.post("/chat/messages", json={"message": "new"}, headers=headers)
    finally:
        app.dependency_overrides.pop(get_chat_config, None)

    assert second.status_code == 200
    assert third.status_code == 409
    assert len(transcript.json()) == 4  # the rejected message was not stored
    assert fresh.status_code == 200
