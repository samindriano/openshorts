FROM python:3.11-slim

WORKDIR /publisher

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium git \
    && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY publisher/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

COPY publisher/ ./publisher/

ENV PYTHONUNBUFFERED=1
ENV PUBLISHER_OUTPUT_DIR=/output
ENV PUBLISHER_DATA_DIR=/publisher-data

RUN useradd --create-home --uid 10001 publisher \
    && mkdir -p /output /publisher-data /ms-playwright \
    && chown -R publisher:publisher /publisher /ms-playwright
USER publisher

EXPOSE 3200
CMD ["uvicorn", "publisher.app:app", "--host", "0.0.0.0", "--port", "3200"]
