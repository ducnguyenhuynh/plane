FROM node:22.12.0-bookworm-slim AS opencode-download

ARG OPENCODE_VERSION=2.2.6
ARG OPENCODE_SHA256_AMD64=08bb21d6326ce740d91cce7ebe88a6536933f6e1e30353b91a06b01f25da306c
ARG OPENCODE_SHA256_ARM64=f2d27600c367e45057d6a16f6a5c2aa8d77b41a9b10d4bc3d849c2f88870e944

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "$(dpkg --print-architecture)" in \
        amd64) target=x64; checksum="$OPENCODE_SHA256_AMD64" ;; \
        arm64) target=arm64; checksum="$OPENCODE_SHA256_ARM64" ;; \
        *) echo "unsupported architecture" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/opencode.tar.gz \
        "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-${target}.tar.gz"; \
    echo "${checksum}  /tmp/opencode.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/opencode.tar.gz -C /tmp opencode; \
    install -m 0755 /tmp/opencode /usr/local/bin/opencode

FROM node:22.12.0-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ripgrep ca-certificates python3 python3-pip python3-venv build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=opencode-download /usr/local/bin/opencode /usr/local/bin/opencode
COPY --from=golang:1.26.9-bookworm /usr/local/go /usr/local/go

RUN groupadd -g 10001 agent \
    && useradd -u 10001 -g 10001 -m -d /home/agent -s /bin/sh agent \
    && mkdir -p /home/agent/.cache /home/agent/.config /home/agent/.local/share/opencode /home/agent/.local/state /home/agent/.plane-skills /home/agent/.package-cache /workspace \
    && chown -R 10001:10001 /home/agent /workspace

COPY sandbox-entrypoint.sh /usr/local/bin/sandbox-entrypoint.sh
RUN chmod 0755 /usr/local/bin/sandbox-entrypoint.sh

ENV HOME=/home/agent
ENV PATH=/usr/local/go/bin:$PATH
ENV GOPATH=/home/agent/.package-cache/go
ENV GOCACHE=/home/agent/.package-cache/go-build
ENV npm_config_cache=/home/agent/.package-cache/npm
ENV PIP_CACHE_DIR=/home/agent/.package-cache/pip

USER 10001:10001

ENTRYPOINT ["/usr/local/bin/sandbox-entrypoint.sh"]
