# tomabotchi server. Built and started by `docker compose up -d` (compose.yml).
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY docker/entrypoint.sh /usr/local/bin/tomabotchi-entrypoint
RUN pip install --no-cache-dir . && useradd --uid 1000 --create-home keeper

# No USER line: the entrypoint joins the Docker socket's group as root, then drops to uid 1000.
ENTRYPOINT ["tomabotchi-entrypoint"]
EXPOSE 8770
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8770/healthz', timeout=2)"
CMD ["tomabotchi", "serve", "--host", "0.0.0.0"]
