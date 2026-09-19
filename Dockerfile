FROM 192.168.1.40:5001/library/python:3.14.7-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    net-tools \
    network-manager \
    systemd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py index.html ./

EXPOSE 4000

CMD ["python3", "server.py", "--port", "4000"]