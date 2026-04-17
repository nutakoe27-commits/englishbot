FROM python:3.11-slim

WORKDIR /app

# Системные зависимости:
# - curl: нужен для Docker healthcheck
# - build-essential, libffi-dev: нужны для сборки некоторых Python-пакетов
#   (google-genai зависит от grpcio, которому может потребоваться компиляция)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости и устанавливаем
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код приложения
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
