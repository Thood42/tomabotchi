# tomabotchi server. Built and started by platform/bin/stack (platform/stack/tomabotchi.compose.yml).
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 1000 --create-home keeper

USER 1000
EXPOSE 8770
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8770/', timeout=2)"
CMD ["tomabotchi", "serve", "--host", "0.0.0.0"]
