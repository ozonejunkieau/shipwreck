FROM --platform=linux/amd64 python:3.12-slim AS builder
FROM --platform=$BUILDPLATFORM alpine:3.18 AS downloader
FROM scratch AS final
