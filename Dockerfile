FROM python:3.11-slim-bookworm AS builder
WORKDIR /build
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates age \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels mcp-locaweb-sftp \
    && rm -rf /wheels
USER app
WORKDIR /home/app
ENTRYPOINT ["mcp-locaweb-sftp-mcp"]
