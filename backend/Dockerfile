# TankVitals — imagem do backend (ingestor MQTT + API FastAPI)
# Construída pelo perfil "app" do infra/docker-compose.yml.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tools ./tools

EXPOSE 8000

# Sem --reload: o recarregador duplicaria o ingestor MQTT (dois clientes com o
# mesmo client_id se derrubam em laço).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
