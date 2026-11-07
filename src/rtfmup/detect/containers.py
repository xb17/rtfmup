"""Dockerfile and compose files: services (known images) and published ports."""

from __future__ import annotations

import re
from typing import Any

import yaml

from rtfmup.detect._common import MAX_BYTES, Detection, find_key_line
from rtfmup.models import Port, Service

COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

# image name (last path segment, no tag) -> service name
KNOWN_SERVICES: dict[str, str] = {
    "postgres": "postgres",
    "postgresql": "postgres",
    "pgvector": "postgres",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "mongo": "mongodb",
    "mongodb": "mongodb",
    "redis": "redis",
    "valkey": "redis",
    "memcached": "memcached",
    "rabbitmq": "rabbitmq",
    "elasticsearch": "elasticsearch",
    "opensearch": "opensearch",
    "kafka": "kafka",
    "minio": "minio",
    "clickhouse-server": "clickhouse",
    "mailhog": "mailhog",
    "mailpit": "mailpit",
}


def image_service(image: str) -> str | None:
    name = image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0].lower()
    return KNOWN_SERVICES.get(name)


_SPLIT = re.compile(r"(?:\$\{[^}]*\}|[^:])+")
_DEFAULT = re.compile(r"^\$\{[^:}]+:?-(\d+)\}$")


def _to_port(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 0 < value < 65536 else None
    if isinstance(value, str):
        v = value.strip()
        if v.isdigit():
            return _to_port(int(v))
        if m := _DEFAULT.match(v):  # ${PORT:-8000}
            return _to_port(int(m.group(1)))
    return None


def published_port(entry: Any) -> tuple[int | None, str]:
    """Return (host port, text to locate in the file) for one `ports` entry.

    Entries with no fixed host port (ephemeral) return None: we do not guess.
    """
    if isinstance(entry, dict):  # long syntax
        pub = entry.get("published")
        return _to_port(pub), str(pub if pub is not None else entry.get("target", ""))
    if isinstance(entry, int) and not isinstance(entry, bool):
        return None, str(entry)  # "3000": container port only
    if isinstance(entry, str):
        spec = entry.split("/", 1)[0]  # drop /tcp, /udp
        parts = _SPLIT.findall(spec)  # split on ":" outside ${...}
        # [ip:]host:container, or container only
        if len(parts) == 1:
            return None, entry
        host = parts[-2]
        host = (
            host if host.startswith("$") else host.split("-", 1)[0]
        )  # range: first port
        return _to_port(host), entry
    return None, str(entry)


def detect(d: Detection) -> None:
    for name in ("Dockerfile", "dockerfile"):
        if d.exists(name):
            d.has_docker = True
            d.manifest(name, "dockerfile", "containers.dockerfile")
    for path in sorted(d.root.glob("Dockerfile.*")):
        if path.is_file():
            d.has_docker = True
            d.manifest(path.name, "dockerfile", "containers.dockerfile")

    for name in COMPOSE_FILES:
        text = d.read(name)
        if text is None:
            continue
        d.has_docker = True
        d.manifest(name, "compose", "containers.compose")
        _compose(d, name, text)


def _compose(d: Detection, name: str, text: str) -> None:
    if len(text) > MAX_BYTES:
        d.warnings.append(f"{d.relname(name)}: larger than {MAX_BYTES} bytes, skipped")
        return
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        d.warnings.append(
            f"{d.relname(name)}: invalid YAML ({str(exc).splitlines()[0]})"
        )
        return
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return

    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        svc_key = str(svc_name)
        image = svc.get("image") if isinstance(svc.get("image"), str) else None
        known = image_service(image) if image else None
        svc_line = find_key_line(text, "services:", f"{svc_key}:")

        ports: list[tuple[int, int | None]] = []
        for entry in svc.get("ports") or []:
            port, needle = published_port(entry)
            if port is None:
                continue
            line = find_key_line(text, "services:", f"{svc_key}:", "ports:", needle)
            ports.append((port, line))

        if known:
            d.services.append(
                Service(
                    name=known,
                    source="compose",
                    image=image,
                    ports=[p for p, _ in ports],
                    evidence=[d.ref(name, svc_line, "containers.compose")],
                )
            )
        else:
            # Infrastructure ports stay on the Service. Only app ports become Ports.
            for port, line in ports:
                d.ports.append(
                    Port(
                        port=port,
                        url=f"http://localhost:{port}",
                        component=d.component,
                        evidence=[d.ref(name, line, "containers.compose")],
                    )
                )
