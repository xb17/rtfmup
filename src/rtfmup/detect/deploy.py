"""Deployment files (D19): detected and reported, never acted on."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from rtfmup.detect._common import MAX_BYTES, MAX_FILES, Detection, find_key_line
from rtfmup.models import Deployment

# file name -> (Deployment.kind, manifest kind)
SIMPLE_FILES: dict[str, tuple[str, str]] = {
    "fly.toml": ("fly", "fly_toml"),
    "vercel.json": ("vercel", "vercel_json"),
    "netlify.toml": ("netlify", "netlify_toml"),
    "render.yaml": ("render", "render_yaml"),
    "render.yml": ("render", "render_yaml"),
    "railway.json": ("railway", "railway_config"),
    "railway.toml": ("railway", "railway_config"),
    "wrangler.toml": ("cloudflare_wrangler", "wrangler_config"),
    "wrangler.json": ("cloudflare_wrangler", "wrangler_config"),
    "wrangler.jsonc": ("cloudflare_wrangler", "wrangler_config"),
    "serverless.yml": ("serverless", "serverless_yaml"),
    "serverless.yaml": ("serverless", "serverless_yaml"),
}

K8S_DIRS = ("k8s", "kubernetes", "deploy", "deployment", "manifests", "charts")
HEROKU_KEYS = {"buildpacks", "addons", "formation", "stack", "environments"}


def detect(d: Detection) -> None:
    _procfile(d)
    for name, (kind, mkind) in SIMPLE_FILES.items():
        if d.exists(name):
            d.manifest(name, mkind, f"deploy.{kind}")
            d.deployments.append(
                Deployment(
                    kind=kind,  # type: ignore[arg-type]
                    file=d.relname(name),
                    summary=_summary(d, name, kind),
                    evidence=[d.ref(name, 1, f"deploy.{kind}")],
                )
            )
    _heroku_app_json(d)
    _k8s_family(d)


def _summary(d: Detection, name: str, kind: str) -> str | None:
    text = d.read(name)
    if text is None:
        return None
    try:
        if name.endswith(".toml"):
            import tomllib

            data = tomllib.loads(text)
            key = "app" if kind == "fly" else "name"
            return f"{key}: {data[key]}" if isinstance(data.get(key), str) else None
        if kind == "serverless":
            data = yaml.safe_load(text)
            svc = data.get("service") if isinstance(data, dict) else None
            if isinstance(svc, dict):
                svc = svc.get("name")
            return f"service: {svc}" if isinstance(svc, str) else None
    except Exception:  # noqa: BLE001 - summary is best effort; the file is still reported
        return None
    return None


def _procfile(d: Detection) -> None:
    text = d.read("Procfile")
    if text is None:
        return
    d.manifest("Procfile", "procfile", "deploy.procfile")
    entries: list[tuple[str, str, int]] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        proc, cmd = line.split(":", 1)
        if proc.strip() and cmd.strip():
            entries.append((proc.strip(), cmd.strip(), n))
    if not entries:
        return
    d.deployments.append(
        Deployment(
            kind="procfile",
            file=d.relname("Procfile"),
            summary="; ".join(f"{p}: {c}" for p, c, _ in entries),
            evidence=[d.ref("Procfile", entries[0][2], "deploy.procfile")],
        )
    )
    for proc, cmd, n in entries:
        if proc == "web":
            d.command(
                cmd,
                shell="sh",
                category="run",
                reason="procfile:web",
                section_path=["Procfile"],
                file="Procfile",
                line=n,
                detector="deploy.procfile",
            )


def _heroku_app_json(d: Detection) -> None:
    text = d.read("app.json")
    if text is None:
        return
    data = d.json("app.json", text)
    # app.json is shared with other tools (Expo, ...): require Heroku-only keys.
    if data is None or "expo" in data or not (HEROKU_KEYS & set(data)):
        return
    d.manifest("app.json", "heroku_app_json", "deploy.heroku_app_json")
    d.deployments.append(
        Deployment(
            kind="heroku_app_json",
            file=d.relname("app.json"),
            summary=None,
            evidence=[d.ref("app.json", 1, "deploy.heroku_app_json")],
        )
    )


def _yaml_files(d: Detection) -> list[Path]:
    """Root-level yaml plus yaml under the conventional dirs. No symlinks, capped."""
    found: list[Path] = [
        p
        for p in sorted(d.root.iterdir())
        if p.suffix in (".yaml", ".yml") and p.is_file() and not p.is_symlink()
    ]
    for sub in K8S_DIRS:
        base = d.root / sub
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(
                x for x in dirnames if x != "templates"
            )  # Helm templates
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if p.suffix in (".yaml", ".yml") and not p.is_symlink():
                    found.append(p)
    return found


def _k8s_family(d: Detection) -> None:
    files = _yaml_files(d)
    if len(files) > MAX_FILES:
        d.warnings.append(
            f"deploy: {len(files)} YAML files, only the first {MAX_FILES} read"
        )
        files = files[:MAX_FILES]
    for path in files:
        rel = path.relative_to(d.root).as_posix()
        if path.stat().st_size > MAX_BYTES:
            d.warnings.append(
                f"{d.relname(rel)}: larger than {MAX_BYTES} bytes, skipped"
            )
            continue
        text = d.read(rel)
        if text is None:
            continue
        lower = path.name.lower()
        if lower in ("chart.yaml", "chart.yml"):
            _helm(d, rel, text)
            continue
        if lower in ("kustomization.yaml", "kustomization.yml"):
            d.manifest(rel, "kustomization", "deploy.kustomize")
            d.deployments.append(
                Deployment(
                    kind="kustomize",
                    file=d.relname(rel),
                    summary=None,
                    evidence=[d.ref(rel, 1, "deploy.kustomize")],
                )
            )
            continue
        _k8s_docs(d, rel, text)


def _helm(d: Detection, rel: str, text: str) -> None:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = None
    name = data.get("name") if isinstance(data, dict) else None
    d.manifest(rel, "helm_chart", "deploy.helm")
    d.deployments.append(
        Deployment(
            kind="helm",
            file=d.relname(rel),
            summary=f"helm chart {name}" if isinstance(name, str) else None,
            evidence=[d.ref(rel, 1, "deploy.helm")],
        )
    )


def _k8s_docs(d: Detection, rel: str, text: str) -> None:
    # Cheap pre-check so ordinary YAML (CI configs, ...) is not fully parsed.
    if "apiVersion" not in text or "kind" not in text:
        return
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return  # not parseable YAML: not a k8s manifest we can describe
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        api, kind = doc.get("apiVersion"), doc.get("kind")
        if not isinstance(api, str) or not isinstance(kind, str):
            continue
        meta = doc.get("metadata")
        name = meta.get("name") if isinstance(meta, dict) else None
        label = f"{kind}/{name}" if isinstance(name, str) else kind
        d.deployments.append(
            Deployment(
                kind="k8s",
                file=d.relname(rel),
                summary=f"k8s {label}",
                evidence=[
                    d.ref(rel, find_key_line(text, f"kind: {kind}"), "deploy.k8s")
                ],
            )
        )
