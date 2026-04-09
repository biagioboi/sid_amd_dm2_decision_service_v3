FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2 and spreadsheet conversion used by the
# Shanghai T2DM dataset-driven digital twin endpoints.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libreoffice-calc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY knowledge ./knowledge
COPY README.md ./

# The dataset is expected to be mounted at runtime, e.g. via docker-compose.
RUN mkdir -p /app/dataset

ENV PYTHONUNBUFFERED=1

EXPOSE 9001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9001"]
