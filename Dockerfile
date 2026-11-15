FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY data/perfil.json ./data/perfil.json

# data/ e docs/ devem ser montados como volume para persistir o SQLite e o dashboard:
#   docker run --rm --env-file .env -v "$PWD/data:/app/data" -v "$PWD/docs:/app/docs" robo-vagas
VOLUME ["/app/data", "/app/docs"]
ENTRYPOINT ["python", "-m", "src.main"]
