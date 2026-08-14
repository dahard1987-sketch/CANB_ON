FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py update_excel.py ./
COPY templates ./templates
COPY static ./static

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["sh", "-c", "exec gunicorn --bind :${PORT:-8080} --workers 1 --threads 8 --timeout 900 app:app"]
