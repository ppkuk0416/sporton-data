# syntax=docker/dockerfile:1
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 postgresql-client && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock requirements-deep-learning.lock pyproject.toml ./
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir -r requirements-deep-learning.lock
COPY sporton ./sporton
COPY config ./config
RUN pip install --no-deps --no-build-isolation . && useradd --create-home sporton && mkdir /data && chown sporton:sporton /data
USER sporton
ENV SPORTON_HOME=/data
EXPOSE 8000
CMD ["sporton","serve","--host","0.0.0.0"]
