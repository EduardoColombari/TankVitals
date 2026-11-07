FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

WORKDIR /opt/plat06
RUN pip install --no-cache-dir pika==1.3.2
COPY infra/rabbitmq/plat05/fault_proxy.py ./fault_proxy.py
COPY infra/rabbitmq/plat05/writer_influx_test.py ./writer_influx_test.py
