FROM 192.168.1.40:5001/library/python:3.15.0-slim

ARG APP_UID=1000
ARG APP_GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    net-tools \
    network-manager \
    dnsmasq-base \
    systemd \
    sudo \
    passwd \
    libnotify-bin \
    && rm -rf /var/lib/apt/lists/*

# sudo requires an unlocked passwd entry, even for a NOPASSWD command. This
# non-login account gets a random password that is never emitted or used.
RUN if ! getent passwd "${APP_UID}" >/dev/null; then \
    if ! getent group "${APP_GID}" >/dev/null; then \
    groupadd --gid "${APP_GID}" system-manager; \
    fi; \
    useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home \
    --home-dir /nonexistent --shell /usr/sbin/nologin system-manager; \
    fi \
    && runtime_user="$(getent passwd "${APP_UID}" | cut -d: -f1)" \
    && printf '%s:%s\n' "${runtime_user}" "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
    | chpasswd

WORKDIR /app

# Flask application
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py server.py apply_music_tags.py media_path.py ./
COPY system_manager/ system_manager/
COPY system_manager/network_manager/dnsmasq_helper.py /usr/local/sbin/system-manager-dns-helper
RUN chmod 0755 /usr/local/sbin/system-manager-dns-helper \
    && printf '%s\n' 'ALL ALL=(root) NOPASSWD: /usr/local/sbin/system-manager-dns-helper' \
    > /etc/sudoers.d/system-manager-dns \
    && chmod 0440 /etc/sudoers.d/system-manager-dns \
    && visudo -cf /etc/sudoers.d/system-manager-dns
COPY templates/ templates/
COPY static/ static/

EXPOSE 4000

CMD ["gunicorn", "--bind", "0.0.0.0:4000", "--workers", "1", "--threads", "4", "run:app"]