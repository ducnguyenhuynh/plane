FROM python:3.12.5-alpine

RUN apk add --no-cache docker-cli

WORKDIR /app

COPY runner/ ./runner/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "runner"]
