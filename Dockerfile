# Public demo image (e.g. a Hugging Face Space): pilot data only, Linux resource-limit executor, port 7860.
#   docker build -t trialproof . && docker run -p 7860:7860 -e TP_LLM_BASE_URL=... -e TP_LLM_MODEL=... -e TP_LLM_API_KEY=... trialproof
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npx vite build

FROM rocker/r-ver:4.5.1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv util-linux \
    && rm -rf /var/lib/apt/lists/*
RUN install2.r --error --skipinstalled dplyr tidyr haven readr jsonlite tibble
RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app
COPY --chown=user server/requirements.txt server/requirements.txt
RUN python3 -m venv server/.venv && server/.venv/bin/pip install --no-cache-dir -r server/requirements.txt
COPY --chown=user server/ server/
COPY --chown=user --from=web /web/dist web/dist
# the public CDISC pilot datasets, checked against their pinned SHA-256 at build time
RUN cd server && .venv/bin/python -c "import asyncio, httpx; from tp.datasets import PILOT_DIR, PILOT_SHA, PILOT_URL, sha256; \
PILOT_DIR.mkdir(parents=True, exist_ok=True); \
[(PILOT_DIR / f).write_bytes(httpx.get(PILOT_URL.format(f), follow_redirects=True, timeout=120).content) for f in PILOT_SHA]; \
assert all(sha256(PILOT_DIR / f) == h for f, h in PILOT_SHA.items()), 'pilot checksum mismatch'"
ENV TP_PUBLIC=1 TP_EXECUTOR=rlimit TP_RUN_TIMEOUT=60 TP_RUN_MEMORY_MB=1536
EXPOSE 7860
CMD ["sh", "-c", "cd server && exec .venv/bin/uvicorn tp.main:app --host 0.0.0.0 --port 7860 --proxy-headers --forwarded-allow-ips='*'"]
