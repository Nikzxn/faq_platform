import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase
from qdrant_client.models import Distance, VectorParams, PointStruct

from assistant import Assistant


def _fake_requests_post(url, headers=None, data=None, verify=None):
    class Resp:
        def json(self_inner):
            return {
                "access_token": "token-from-auth",
                "expires_at": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000),
            }

    return Resp()


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload_factory):
        self.payload_factory = payload_factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, data=None, ssl=None):
        return FakeResponse(self.payload_factory(url))


class TestAssistant(SimpleTestCase):
    def setUp(self):
        os.environ.setdefault("GIGATOKEN", "dummy-token")
        os.environ.setdefault("QDRANT_IN_MEMORY", "1")
        Assistant._Assistant__instance = None  # reset singleton across tests

        self.requests_patcher = patch("assistant.requests.post", side_effect=_fake_requests_post)
        self.requests_patcher.start()
        self.addCleanup(self.requests_patcher.stop)

        def payload_factory(url):
            if url.endswith("/embeddings"):
                return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
            if url.endswith("/oauth"):
                return {
                    "access_token": "refreshed-token",
                    "expires_at": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000),
                }
            return {"choices": [{"message": {"content": "generated answer"}}]}

        self.session_patcher = patch("assistant.aiohttp.ClientSession", lambda: FakeSession(payload_factory))
        self.session_patcher.start()
        self.addCleanup(self.session_patcher.stop)

        self.assistant = Assistant()
        client = self.assistant._Assistant__qdrant
        collection = self.assistant._Assistant__collection
        client.recreate_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=3, distance=Distance.COSINE),
        )
        client.upsert(
            collection_name=collection,
            points=[
                PointStruct(id=1, vector=[0.1, 0.2, 0.3], payload={"question": "Q1", "answer": "A1", "related_questions": ["rq1"]}),
                PointStruct(id=2, vector=[0.1, 0.2, 0.3], payload={"question": "Q2", "answer": "A2", "related_questions": ["rq2", "rq3"]}),
            ],
        )

    def test_get_embedding_returns_vector_and_refreshes_token(self):
        self.assistant._Assistant__expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.assistant._Assistant__access_token = "expired"
        embedding = asyncio.run(self.assistant.get_embedding("hello"))
        self.assertEqual(embedding, [0.1, 0.2, 0.3])
        self.assertEqual(self.assistant._Assistant__access_token, "refreshed-token")

    def test_answers_returns_payload_answers(self):
        answers = asyncio.run(self.assistant.answers("hello"))
        self.assertEqual(sorted(answers), ["A1", "A2"])

    def test_call_uses_process_message_result(self):
        expected = Assistant.Response(answer="done", related_questions=["r1"])
        with patch.object(Assistant, "_Assistant__process_message", new=AsyncMock(return_value=expected)):
            result = asyncio.run(self.assistant("payload", max_related=1))
        self.assertEqual(result.answer, "done")
        self.assertEqual(result.related_questions, ["r1"])
