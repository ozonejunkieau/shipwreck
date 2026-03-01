# API service Dockerfile.
# Demonstrates builds_from edges — Shipwreck traces the FROM chain.

FROM registry.example.com/base/python:3.12

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
