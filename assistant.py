import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps

import aiohttp
import requests
from gigachat.models.assistants import Assistant
from qdrant_client import QdrantClient


class Assistant:
    __instance = None
    __initialized = False

    @dataclass
    class Response:
        answer: str
        related_questions: list[str]

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super(Assistant, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        if self.__initialized:
            return

        self._initialized = True
        self.__queue = []
        self.__authurl = "https://ngw.devices.sberbank.ru:9443/api/v2"
        self.__baseurl = "https://gigachat.devices.sberbank.ru/api/v1"
        qdrant_host = os.getenv("QDRANT_HOST", "qdrant")
        qdrant_port = os.getenv("QDRANT_PORT", "6333")
        qdrant_url = os.getenv("QDRANT_URL", f"http://{qdrant_host}:{qdrant_port}")

        self.__qdrant = QdrantClient(url=qdrant_url)
        self.__collection = os.getenv("QDRANT_COLLECTION", "que")

        self.__gigatoken = os.getenv("GIGATOKEN", None)
        if not self.__gigatoken:
            raise Exception("Необходимо указать переменную окружения GIGATOKEN для подключения GigaChat.")

        response = requests.post(
            f"{self.__authurl}/oauth",
            headers={
                "Authorization": f"Basic {self.__gigatoken}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "RqUID": str(uuid.uuid4()),
            },
            data={
                "scope": "GIGACHAT_API_PERS",
            },
            verify=False
        )

        response = response.json()
        self.__access_token = response["access_token"]
        self.__expires_at = datetime.fromtimestamp(response["expires_at"] / 1000, timezone.utc)

    @staticmethod
    def authorized(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            if datetime.now(timezone.utc) >= self.__expires_at:
                async with aiohttp.ClientSession() as session:
                    response = await session.post(
                        f"{self.__authurl}/oauth",
                        headers={
                            "Authorization": f"Basic {self.__gigatoken}",
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Accept": "application/json",
                            "RqUID": str(uuid.uuid4()),
                        },
                        data={
                            "scope": "GIGACHAT_API_PERS",
                        },
                        ssl=False
                    )
                    response = await response.json()

                self.__access_token = response["access_token"]
                self.__expires_at = datetime.fromtimestamp(response["expires_at"] / 1000, timezone.utc)

            return await func(self, *args, **kwargs)

        return wrapper

    @authorized
    async def get_embedding(self, message: str) -> list[float]:
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                f"{self.__baseurl}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.__access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                data=json.dumps(
                    {
                        "model": "Embeddings",
                        "input": message
                    }
                ),
                ssl=False
            )
            return (await response.json())["data"][0]["embedding"]

    @authorized
    async def __process_message(self, message: str, max_related: int) -> Response:

        query_vec = await self.get_embedding(message)
        hits = self.__qdrant.query_points(
            collection_name=self.__collection,
            query=query_vec,
            limit=5,
            with_payload=True,
        ).points

        related_questions = []
        for hit in hits:
            related_questions.extend(hit.payload["related_questions"])

        data = {
            "model": "GigaChat-2",
            "messages": [
                {
                    "role": "system",
                    "content": "Ты - ассистент поддержки Ростелкома. "
                               "Твоя задача отвечать максимально точно и развернуто на вопросы пользователя. "
                               "Общайся уважительно и вежливо. "
                               "Представляйся как Ростислав."
                               "\n\n"
                               "Если пользователь зовет прямо оператора, то отвечай только: 'оператор'"
                               "\n\n\n"
                               "При ответе опирайся на следующие данные:\n"
                               + "\n\n".join(
                        "\n".join(
                            [
                                f"Вопрос: {hit.payload['question']}\nОтвет: {hit.payload['answer']}"
                                for hit in hits
                            ])
                    )
                },
                {
                    "role": "user",
                    "content": message
                }
            ]
        }

        async with aiohttp.ClientSession() as session:
            response = await session.post(
                f"{self.__baseurl}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.__access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                data=json.dumps(data),
                ssl=False
            )
            return Assistant.Response(
                answer=(await response.json())["choices"][0]["message"]["content"],
                related_questions=related_questions[:max_related]
            )

    async def __call__(self, message: str, max_related: int = 5) -> Response:
        task_id = str(uuid.uuid4())
        self.__queue.append(task_id)

        while self.__queue[0] != task_id:
            await asyncio.sleep(0.1)

        self.__queue.pop(0)

        return await self.__process_message(message, max_related)

    async def answers(self, message: str) -> list[str]:
        query_vec = await self.get_embedding(message)
        hits = self.__qdrant.query_points(
            collection_name=self.__collection,
            query=query_vec,
            limit=10,
            with_payload=True,
        ).points

        related_questions = []
        for hit in hits:
            related_questions.append(hit.payload["answer"])

        return related_questions[:10]
