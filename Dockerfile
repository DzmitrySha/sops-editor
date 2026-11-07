# syntax=docker/dockerfile:1
FROM python:3.12-slim

ARG TARGETARCH=amd64
ARG SOPS_VERSION=3.13.3
ARG AGE_VERSION=1.3.2

# uv installs the Python package; the binary is copied from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    SOPS_EDITOR_FRONTEND_DIR=/app/frontend \
    SOPS_EDITOR_STORAGE=/data \
    SOPS_EDITOR_ROOTS=/workspace|/data/repos

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        gnupg \
        openssh-client \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Pin helper binaries and verify checksums. Build with:
#   docker build --build-arg TARGETARCH=arm64 .
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) sops_sha="e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b"; age_sha="cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10" ;; \
      arm64) sops_sha="53b0abacd38ef1b12a66d6c100956691b9cefce018d91f81e73ddf7438b94d77"; age_sha="6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4" ;; \
      *) echo "Unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/sops "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${TARGETARCH}"; \
    echo "${sops_sha}  /tmp/sops" | sha256sum -c -; \
    install -m 0755 /tmp/sops /usr/local/bin/sops; \
    curl -fsSL -o /tmp/age.tar.gz "https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    echo "${age_sha}  /tmp/age.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/age.tar.gz -C /tmp; \
    install -m 0755 "/tmp/age/age" "/tmp/age/age-keygen" /usr/local/bin/; \
    rm -rf /tmp/sops /tmp/age.tar.gz /tmp/age; \
    sops --version; \
    age --version; \
    age-keygen --version

WORKDIR /app
COPY backend /app/backend
RUN uv pip install --system --no-cache /app/backend \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx \
    && python -c "import pathlib, app, os; p = pathlib.Path(app.__file__).parent / 'sops_editor_helper.py'; os.chmod(p, 0o755); print(p)"
COPY frontend /app/frontend

RUN mkdir -p /data /workspace /keys \
    && chmod 1777 /tmp \
    && printf '#!/bin/sh\ncase "$1" in\n  *sername*) printf "%%s\\n" "$SOPS_EDITOR_GIT_USERNAME" ;;\n  *assword*) printf "%%s\\n" "$SOPS_EDITOR_GIT_TOKEN" ;;\n  *) printf "\\n" ;;\nesac\n' > /usr/local/bin/sops-editor-askpass \
    && chmod 0755 /usr/local/bin/sops-editor-askpass

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
