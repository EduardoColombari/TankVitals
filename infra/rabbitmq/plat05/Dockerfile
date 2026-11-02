FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

WORKDIR /opt/plat05
COPY infra/rabbitmq/plat05/requirements-test.txt ./requirements-test.txt
RUN pip install --no-cache-dir -r requirements-test.txt
COPY infra/rabbitmq/plat05/integration_test.py ./integration_test.py

ENTRYPOINT ["python", "/opt/plat05/integration_test.py"]
