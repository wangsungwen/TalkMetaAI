FROM node:20-bookworm-slim AS client-build

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/client/package.json apps/client/package.json

RUN corepack enable \
    && corepack prepare pnpm@9.15.9 --activate \
    && pnpm install --frozen-lockfile

COPY apps/client apps/client
RUN pnpm --filter @talkmateai/client build

FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    TALKMETA_LIGHTWEIGHT=auto \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        espeak-ng \
        ffmpeg \
        git \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY apps/server apps/server
COPY --from=client-build /app/apps/client/out apps/client/out

WORKDIR /app/apps/server

RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu \
        torch==2.6.0 \
        torchvision==0.21.0 \
    && pip install \
        "fastapi[standard]>=0.115.6" \
        "uvicorn>=0.34.3" \
        "transformers==4.49.0" \
        "accelerate>=1.7.0" \
        "soundfile==0.13.1" \
        "pillow==11.0.0" \
        "scipy==1.15.2" \
        "backoff==2.2.1" \
        "peft==0.13.2" \
        kokoro \
        numpy \
        packaging \
        requests \
        websockets \
        wheel

EXPOSE 7860

CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"
