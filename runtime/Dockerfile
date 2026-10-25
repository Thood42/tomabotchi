# tomabotchi pet runtime: everything a pet needs, laid out under /opt so any glibc home can carry it.
# Build context is platform/tomabotchi (so rules.py is shared with the server). Never run directly:
# each pet image is `FROM <home>` + `COPY --from=` this image (see pet.Dockerfile.tmpl).
#
#   /opt/hermes       Hermes Agent with its own standalone Python 3.14 (from the pinned upstream image)
#   /opt/tomabotchi   petd, the leash plugin, code-server, smoke test

ARG HERMES_IMAGE=nousresearch/hermes-agent@sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d
ARG CODE_SERVER_VERSION=4.140.0

FROM ${HERMES_IMAGE} AS hermes
# Drop what pets don't use: the bundled browser and ffmpeg (~800 MB). The browser home brings its own.
RUN rm -rf /opt/hermes/tools/chromium-* /opt/hermes/tools/ffmpeg-* /opt/hermes/tools/neutts_samples
# Upstream's venv points at /usr/local/bin/python3, which a home image owns (python:3.12-slim has its
# own there). Point it at Hermes's bundled standalone Python instead, so homes stay untouched.
RUN py="$(ls -d /opt/hermes/tools/python-*-linux-x64)/bin" \
 && ln -sf "$py/python3" /opt/hermes/.venv/bin/python \
 && sed -i "s#^home = .*#home = $py#" /opt/hermes/.venv/pyvenv.cfg

FROM debian:trixie-slim AS fetch
ARG CODE_SERVER_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/tomabotchi/code-server \
 && curl -fsSL "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-amd64.tar.gz" \
    | tar -xz --strip-components=1 -C /opt/tomabotchi/code-server

FROM scratch
COPY --from=hermes /opt/hermes /opt/hermes
COPY --from=fetch /opt/tomabotchi/code-server /opt/tomabotchi/code-server
COPY runtime/petd /opt/tomabotchi/petd
COPY runtime/leash/tomabotchi_leash /opt/tomabotchi/leash/tomabotchi_leash
COPY src/tomabotchi/rules.py /opt/tomabotchi/leash/tomabotchi_leash/rules.py
COPY runtime/bin /opt/tomabotchi/bin
