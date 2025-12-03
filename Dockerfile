FROM python:3.12-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    bzip2 \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код проекта
COPY . .

# Сбор статических файлов
RUN mkdir -p /app/staticfiles

# Запускаем сервер
CMD ["uvicorn", "DjangoProject.asgi:application", "--host", "0.0.0.0", "--port", "8000"]