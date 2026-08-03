FROM python:3.11-slim

WORKDIR /srv

COPY packages/shared-schemas /srv/packages/shared-schemas
COPY packages/model-gateway /srv/packages/model-gateway
COPY packages/decision-engine /srv/packages/decision-engine
COPY apps/api /srv/apps/api

RUN pip install --no-cache-dir \
    -e /srv/packages/shared-schemas \
    -e /srv/packages/model-gateway \
    -e /srv/packages/decision-engine \
    -e /srv/apps/api

WORKDIR /srv/apps/api

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
