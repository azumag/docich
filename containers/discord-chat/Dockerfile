FROM python:3.12.15-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS runtime
WORKDIR /opt/docich
COPY requirements-discord.txt ./requirements-discord.txt
RUN python -m pip install --no-cache-dir -r requirements-discord.txt \
    && mkdir -p /var/lib/docich-discord \
    && chown 65532:65532 /var/lib/docich-discord \
    && chmod 0700 /var/lib/docich-discord
COPY src/docich/__init__.py src/docich/discord_chat.py src/docich/discord_memory.py ./src/docich/
COPY src/docich/reply_routing.py src/docich/reply_research.py \
     src/docich/reply_research_bridge.py src/docich/reply_research_egress.py ./src/docich/
COPY src/docich/semantic_decision/ ./src/docich/semantic_decision/
COPY src/docich/comment/prompts/comment_persona_main.md ./src/docich/comment/prompts/comment_persona_main.md
COPY containers/discord-chat/volume.py /opt/docich/volume.py
ENV PYTHONPATH=/opt/docich/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOME=/tmp DOCICH_DISCORD_MEMORY_DIR=/var/lib/docich-discord \
    DOCICH_DISCORD_ENABLED=0 DOCICH_ALLOW_REAL_AI=0 \
    DOCICH_REPLY_ROUTING_ENABLED=0 DOCICH_REPLY_RESEARCH_ENABLED=0
USER 65532:65532
ENTRYPOINT ["python", "-m", "docich.discord_chat"]

FROM runtime AS test
USER root
COPY requirements-test.txt ./requirements-test.txt
RUN python -m pip install --no-cache-dir -r requirements-test.txt
COPY tests/test_discord_chat.py tests/test_discord_memory.py tests/test_discord_container.py ./tests/
COPY tests/test_reply_routing.py tests/test_reply_research.py ./tests/
COPY tests/fixtures/reply_routing_canary.json ./tests/fixtures/reply_routing_canary.json
USER 65532:65532
ENTRYPOINT ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp=/tmp/discord-pytest", "tests"]
