# TankVitals — imagem do backend (ingestor MQTT + API FastAPI)
# Construída pelo perfil "app" do infra/docker-compose.yml.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt backend/requirements-iot.txt backend/plat-requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-iot.txt -r plat-requirements.txt

COPY backend/app ./app
COPY backend/tools ./tools
COPY scripts ./scripts

RUN groupadd --gid 10001 --system app && useradd --uid 10001 --system --gid app --create-home app \
    && mkdir -p /var/lib/tankvitals /var/lib/tankvitals-writer \
    && chown -R app:app /app /var/lib/tankvitals /var/lib/tankvitals-writer
USER app

EXPOSE 8000

# Sem --reload: o recarregador duplicaria o ingestor MQTT (dois clientes com o
# mesmo client_id se derrubam em laço).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
