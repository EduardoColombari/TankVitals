FROM python:3.13-slim

ARG AIO_PIKA_VERSION=10.2.2
RUN pip install --no-cache-dir "aio-pika==${AIO_PIKA_VERSION}" "influxdb-client[async]==1.48.0" \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /run/plat06/secrets \
    && chown -R app:app /run/plat06

WORKDIR /app
COPY backend/app/writer.py /app/writer.py
COPY scripts/bootstrap_influx_writer.py /app/bootstrap_influx_writer.py
USER 10001:10001
