FROM python:3.12-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    bzip2 \
    build-essential \
    pkg-config \
    libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

# Установка системных зависимостей для Playwright / Chromium
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxrender1 \
    libxshmfence1 \
    libgbm1 \
    libpango-1.0-0 \
    libgtk-3-0 \
    libasound2 \
    xvfb \
 && rm -rf /var/lib/apt/lists/*
# Создаем рабочую директорию
WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Копируем код проекта
COPY . .
COPY /app/tests /app/tests
# Сбор статических файлов
RUN mkdir -p /app/staticfiles

# Запускаем сервер
CMD ["uvicorn", "DjangoProject.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
