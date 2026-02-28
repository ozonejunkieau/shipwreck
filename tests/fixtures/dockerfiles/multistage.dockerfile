FROM python:3.12-slim AS builder
RUN pip install --user flask gunicorn

FROM python:3.12-slim AS runtime
COPY --from=builder /root/.local /root/.local
CMD ["python", "app.py"]
