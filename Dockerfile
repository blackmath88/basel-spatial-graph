FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY basel-spatial-graph-v0/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY basel-spatial-graph-v0/ ./
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8080}\""]
