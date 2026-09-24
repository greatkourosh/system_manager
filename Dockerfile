FROM 192.168.1.40:5001/library/python:3.14.7-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    net-tools \
    network-manager \
    systemd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Flask application
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py server.py index.html ./
COPY system_manager/ system_manager/
COPY templates/ templates/
COPY static/ static/

EXPOSE 4000

CMD ["gunicorn", "--bind", "0.0.0.0:4000", "--workers", "1", "--threads", "4", "run:app"]