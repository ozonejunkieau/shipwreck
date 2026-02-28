ARG BASE_IMAGE=python:3.12
ARG BUILDER_VERSION=3.12-slim

FROM ${BASE_IMAGE}-slim AS builder
RUN pip install flask

FROM python:${BUILDER_VERSION} AS runtime
CMD ["python", "app.py"]
