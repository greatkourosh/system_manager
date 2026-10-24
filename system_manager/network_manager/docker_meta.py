"""Container names and compose projects, read from the docker socket.

Everything else on the topology page comes from procfs and sysfs, which
cannot answer these two questions: a container's own hostname is its id,
and `environ` (where COMPOSE_PROJECT_NAME would live) is not readable
across the uid boundary. Docker's own database is the only authority, and
the mounted socket is a read-only window into it -- the module issues
GET /containers/json and parses nothing else.
"""
import json
import os
import re
import socket

__all__ = ["container_meta"]

_SOCKET = "/var/run/docker.sock"

_REQUEST = (
    b"GET /v1.40/containers/json?limit=0 HTTP/1.1\r\n"
    b"Host: docker\r\n"
    b"Connection: close\r\n"
    b"\r\n"
)

# HTTP chunked encoding: each chunk is "\r\n<hex length>\r\n<data>\r\n"
# The final chunk is "0\r\n\r\n"
_CHUNK_RE = re.compile(rb"^([0-9a-fA-F]+)\r\n", re.MULTILINE)


def _parse_chunked(body_bytes):
    """Decode chunked transfer encoding, returning the decoded body."""
    result = []
    offset = 0
    while offset < len(body_bytes):
        match = _CHUNK_RE.match(body_bytes, offset)
        if not match:
            break
        length = int(match.group(1), 16)
        if length == 0:
            break
        start = match.end()
        end = start + length
        if end > len(body_bytes):
            break
        result.append(body_bytes[start:end])
        offset = end + 2  # Skip trailing \r\n after data
    return b"".join(result)


def _query():
    """The daemon's container list, or None when the socket is absent."""
    if not os.path.exists(_SOCKET):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(_SOCKET)
            client.sendall(_REQUEST)
            chunks = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError:
        return None

    full = b"".join(chunks)
    # Split headers from body at the first \r\n\r\n
    parts = full.split(b"\r\n\r\n", 1)
    if len(parts) < 2:
        return None

    headers, body = parts[0], parts[1]

    # Check status line
    status_line = headers.split(b"\r\n", 1)[0]
    if b" 200 " not in status_line:
        return None

    # Handle chunked encoding
    if b"Transfer-Encoding: chunked" in headers:
        body = _parse_chunked(body)

    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return None


def _mapping(rows):
    """{id: {"name": ..., "project": ...}} built from the API's rows."""
    meta = {}
    for row in rows:
        id_prefix = (row.get("Id") or "")[:12]
        names = [n.lstrip("/") for n in row.get("Names") or []]
        name = names[0] if names else id_prefix
        labels = row.get("Labels") or {}
        meta[id_prefix] = {
            "name": name,
            "project": labels.get("com.docker.compose.project") or name,
        }
    return meta


def container_meta():
    """{id: {"name": ..., "project": ...}} for every container on the host.

    `project` is the compose stack the container belongs to, taken from the
    label the docker CLI records; containers started by hand carry no label
    and fall back to their own name, which is still a step up from a
    truncated id.
    """
    rows = _query()
    if rows is None:
        return {}
    return _mapping(rows)
