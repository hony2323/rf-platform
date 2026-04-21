FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY server/pyproject.toml /app/server/pyproject.toml
COPY server/src /app/server/src

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir /app/server

WORKDIR /app/server

CMD ["uvicorn", "server.app.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]