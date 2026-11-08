FROM python:3.12-slim

# ffmpeg does the actual converting
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# run as a non-root user
RUN useradd -m appuser
USER appuser

ENV PORT=8000 TRUST_PROXY=1
EXPOSE 8000

# 2 workers x 4 threads. Uploads/conversions are I/O + subprocess bound.
# Long timeout because big conversions can take minutes.
CMD gunicorn app:app --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 600
