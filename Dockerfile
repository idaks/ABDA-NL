# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.runtime.lock ./
RUN /opt/venv/bin/python -m pip install \
      --require-hashes \
      --requirement requirements.runtime.lock \
    && /opt/venv/bin/python -m pip uninstall --yes pip

FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS runtime

ARG ABDA_IMAGE_REVISION=unknown
ARG ABDA_IMAGE_SOURCE=https://github.com/idaks/ABDA-NL

LABEL org.opencontainers.image.source="${ABDA_IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${ABDA_IMAGE_REVISION}" \
      org.opencontainers.image.licenses="GPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ABDA_ENABLE_LLM=1

# Apply the Debian security backport missing from the pinned base image.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade \
      libpcre2-8-0=10.42-1+deb12u1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip uninstall --yes pip \
    && groupadd --system --gid 10001 abda \
    && useradd --system --uid 10001 --gid abda --create-home abda

WORKDIR /srv/abda
COPY --from=build /opt/venv /opt/venv
COPY --chown=abda:abda alembic.ini pyproject.toml README.md LICENSE ./
COPY --chown=abda:abda THIRD_PARTY_NOTICES.md ./
COPY --chown=abda:abda LICENSES ./LICENSES
COPY --chown=abda:abda app ./app
COPY --chown=abda:abda examples ./examples
COPY --chown=abda:abda migrations ./migrations

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/opt/venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"]

CMD ["/opt/venv/bin/python", "-m", "app.cli.serve", "--host", "0.0.0.0", "--port", "8000", "--no-browser", "--allow-non-loopback"]
