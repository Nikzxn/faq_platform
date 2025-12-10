import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path

import django
import pandas as pd
from django.core.management import call_command
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from django.contrib.auth import get_user_model

try:
    importlib.import_module("openpyxl")
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])

    importlib.import_module("openpyxl")

from assistant import Assistant

DATABASE_HOST = 'localhost'
DATABASE_PORT = '5432'
DATABASE_NAME = 'chat_db'
DATABASE_USER = 'chat_user'
DATABASE_PASSWORD = 'strong_psw'
QDRANT_HOST = 'localhost'
QDRANT_PORT = '6333'
COLLECTION = os.getenv("QDRANT_COLLECTION", "que")
EXCEL_FILE = Path("база знаний.xlsx")

def create_superuser(username: str, password: str):
    User = get_user_model()

    if User.objects.filter(username=username).exists():
        print(f"⚠️ Пользователь с username='{username}' уже существует. Ничего не делаю.")
        return

    User.objects.create_superuser(
        username=username,
        password=password,
    )
    print(f"✅ Суперпользователь '{username}' успешно создан.")


async def upload_knowledge_db(qdrant: QdrantClient, assistant: Assistant):
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


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "DjangoProject.settings")
    os.environ.setdefault(
        "GIGATOKEN",
        "OWZiNzRmNzItYTM4YS00ZjdhLTgzOGQtMTQ0ODg4YmVjNzU0OmIwNzVjNzFmLTlhNzgtNDkxOC04Y2U4LTFlOWQzNDY4MDNlZQ=="
    )
    os.environ.setdefault("DATABASE_HOST", DATABASE_HOST)
    os.environ.setdefault("DATABASE_PORT", DATABASE_PORT)
    os.environ.setdefault("DATABASE_NAME", DATABASE_NAME)
    os.environ.setdefault("DATABASE_USER", DATABASE_USER)
    os.environ.setdefault("DATABASE_PASSWORD", DATABASE_PASSWORD)
    os.environ.setdefault("QDRANT_HOST", QDRANT_HOST)
    os.environ.setdefault("QDRANT_PORT", QDRANT_PORT)
    django.setup()
    call_command("migrate")

    qdrant_url = f"http://{QDRANT_HOST}:{QDRANT_PORT}"
    qdrant = QdrantClient(url=qdrant_url)
    assistant = Assistant()

    asyncio.run(upload_knowledge_db(qdrant, assistant))

    create_superuser(
        username="admin",
        password="admin"
    )


if __name__ == "__main__":
    main()
