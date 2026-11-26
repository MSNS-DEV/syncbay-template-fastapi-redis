# syntax=docker/dockerfile:1

# ---- builder: resolve and install locked dependencies into a virtualenv ----
FROM python:3.12-alpine3.22 AS builder
# Only needed if a dependency has no musllinux wheel and must compile; never shipped.
RUN apk add --no-cache build-base libffi-dev
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project \
 && find /opt/venv -name '*.so' -exec strip --strip-unneeded {} + \
 && find /opt/venv \( -name '*.pyx' -o -name '*.pxd' -o -name '*.c' -o -name '*.cpp' -o -name '*.h' \) -delete \
 && find /opt/venv -type d -name tests -prune -exec rm -rf {} + \
 # Ship third-party packages as bytecode only (keeps the image < 150 MB); app code keeps sources.
 && SITE=/opt/venv/lib/python3.12/site-packages \
 && python -m compileall -q -b -j 0 "$SITE" \
 && find "$SITE" -name '*.py' -delete \
 && find "$SITE" -type d -name __pycache__ -prune -exec rm -rf {} +
COPY app ./app
RUN /opt/venv/bin/python -m compileall -q app

# ---- runtime ----
FROM python:3.12-alpine3.22
RUN addgroup -S app && adduser -S -G app -h /app -s /sbin/nologin app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=root:root /src/app ./app
COPY --chown=root:root alembic.ini docker-entrypoint.sh ./
COPY --chown=root:root migrations ./migrations
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/health" >/dev/null || exit 1
STOPSIGNAL SIGTERM
CMD ["/app/docker-entrypoint.sh"]
