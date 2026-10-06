FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY .wheelhouse /tmp/wheels
RUN if [ -n "$(find /tmp/wheels -name '*.whl' -print -quit)" ]; then \
      pip install --no-cache-dir --no-index --find-links=/tmp/wheels -r requirements.lock; \
    else pip install --no-cache-dir -r requirements.lock; fi
COPY hunter ./hunter
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --uid 10001 --create-home hunter && chmod -R a+rX /app
USER hunter
CMD ["sh", "-c", "alembic upgrade head && exec python -m hunter"]
