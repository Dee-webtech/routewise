FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROUTEWISE_SKIP_CPP=1 \
    ROUTEWISE_DB_PATH=/data/routewise.db

WORKDIR /app
COPY pyproject.toml setup.py README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

RUN useradd --create-home app && mkdir /data && chown app /data
USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["python", "-m", "uvicorn", "routewise.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]