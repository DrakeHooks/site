FROM python:3.12-alpine
WORKDIR /app
RUN apk add --no-cache git openssh-client ffmpeg ca-certificates tzdata && update-ca-certificates
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY common.py server.py worker.py /app/
COPY templates /app/templates
COPY static /app/static
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "120", "server:app"]
