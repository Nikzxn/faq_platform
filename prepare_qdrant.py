import asyncio
import json
import os
from pathlib import Path

import pandas as pd
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from assistant import Assistant

qdrant_host = os.getenv("QDRANT_HOST", "qdrant")
qdrant_port = os.getenv("QDRANT_PORT", "6333")
qdrant_url = os.getenv("QDRANT_URL", f"http://{qdrant_host}:{qdrant_port}")

qdrant = QdrantClient(url=qdrant_url)
assistant = Assistant()
COLLECTION = os.getenv("QDRANT_COLLECTION", "que")
EXCEL_FILE = Path("база знаний.xlsx")


async def upload_knowledge_db():
    df = pd.read_excel(EXCEL_FILE)
    points = []

    if not qdrant.collection_exists(COLLECTION):
        qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
        )

    for idx, row in df.iterrows():
        points.append(
            PointStruct(
                id=int(row["Номер вопроса"]),
                vector=await assistant.get_embedding(row["Вопрос"]),
                payload={
                    "question": row["Вопрос"],
                    "answer": row["Ответ"],
                    "related_questions": list(map(lambda x: x.strip(), row["Связанные вопросы"].split("/"))),
                },
            )
        )

    qdrant.upsert(
        collection_name=COLLECTION,
        points=points,
        wait=True,
    )

    # print(await assistant("Привет, как дела?"))


async def test_qdrant():
    print("Всего:", qdrant.count(COLLECTION).count)

    # Семантический поиск по новому вопросу
    query_vec = await assistant.get_embedding("Почему плохо работает интернет?")
    hits = qdrant.query_points(
        collection_name=COLLECTION,
        query=query_vec,
        limit=5,
        with_payload=True,
    ).points

    for h in hits:
        print(h.payload, "score:", h.score)


async def test_assistant():
    # Пример использования
    response: Assistant.Response = await assistant("Почему плохо работает интернет?")
    print(response.answer)
    print(response.related_questions)

async def get_all():
    points, next_offset = qdrant.scroll(
        collection_name=COLLECTION,
        limit=100,
        offset=0,
        with_payload=True,
        # with_vectors=False  # при необходимости добавить векторы
    )
    print(*points, sep="\n")
    print(next_offset)


if __name__ == "__main__":
    # asyncio.run(upload_knowledge_db())
    # asyncio.run(test_qdrant())
    # asyncio.run(test_assistant())
    asyncio.run(get_all())
