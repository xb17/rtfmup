"""Analysis schema v1 (PLAN §2.2). This is a contract: changes need review."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1"

Confidence = Literal[
    "high", "medium", "low"
]  # manifests=high, README=medium/low, CI=low
Platform = Literal["any", "windows", "unix", "macos", "linux"]
Category = Literal["install", "setup", "build", "run", "test", "lint", "other"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(_Model):
    file: str  # repo-relative, forward slashes
    line_start: int | None = None  # 1-based inclusive
    line_end: int | None = None
    detector: str  # e.g. "node.package_json", "readme.fence"


class Runtime(_Model):
    name: Literal[
        "python", "node", "rust", "go", "ruby", "php", "dotnet", "java", "bun", "deno"
    ]
    version_spec: str | None = None  # raw spec as written, e.g. ">=3.11", "20.x", "^18"
    confidence: Confidence
    evidence: list[SourceRef]


class PackageManager(_Model):
    # "npm","pnpm","yarn","bun","uv","pip","poetry","pipenv","cargo","go","bundler",
    # "composer","dotnet"
    name: str
    lockfile: str | None = None
    component: str  # Component.id
    evidence: list[SourceRef]


class ManifestFinding(_Model):
    file: str
    # "pyproject","package_json","cargo_toml","dockerfile","compose","env_template",...
    kind: str
    component: str
    evidence: list[SourceRef]


class Command(_Model):
    id: str  # stable: f"cmd-{n}"
    # normalized single logical command (continuations joined, prompt stripped)
    text: str
    shell: Literal["bash", "sh", "zsh", "powershell", "cmd", "console", "unknown"]
    category: Category
    reason: str  # human-readable classification reason, e.g. "prefix:npm install"
    section_path: list[str]  # heading trail, e.g. ["Getting Started","Installation"]
    platform: Platform = "any"
    variant: str | None = None  # alternative-path group, e.g. "docker", "native"
    needs_shell: bool = False  # D17
    # "sudo","pipe_to_shell","writes_outside_repo","network_install","placeholder"
    flags: list[str] = []
    confidence: Confidence
    evidence: list[SourceRef]


class EnvVar(_Model):
    name: str
    default: str | None = None  # value from template; NEVER populated from a real .env
    required: Literal["required", "optional", "unknown"]
    # name matches KEY|SECRET|TOKEN|PASSWORD|PASS|PRIVATE (case-insensitive)
    secret_like: bool
    description: str | None = None  # trailing comment or README table cell
    evidence: list[SourceRef]


class Service(_Model):  # DBs, caches, queues
    # "postgres","redis","mysql","mongodb","rabbitmq","elasticsearch",...
    name: str
    source: Literal["compose", "readme", "env"]
    image: str | None = None
    ports: list[int] = []
    evidence: list[SourceRef]


class ExternalAccount(_Model):
    name: str  # link text or domain
    url: str
    related_env: list[str] = []
    evidence: list[SourceRef]


class Port(_Model):
    port: int
    url: str | None = None  # e.g. "http://localhost:3000"
    component: str | None = None
    evidence: list[SourceRef]


class Component(_Model):
    id: str  # "root" or repo-relative path with forward slashes
    path: str
    runtimes: list[str]  # Runtime.name values
    package_managers: list[str]
    # package.json scripts / Makefile targets / Justfile recipes (name -> body or "")
    scripts: dict[str, str] = {}
    runnable: bool  # has a plausible run entry


class Conflict(_Model):
    id: str
    message: str
    evidence: list[SourceRef]  # both sides


class Ambiguity(_Model):
    id: str
    question: str  # "Two setup paths found: native or Docker. Which one?"
    options: list[str]
    default: str | None = None
    evidence: list[SourceRef]


class Deployment(_Model):  # D19: detected, never acted on
    kind: Literal[
        "procfile",
        "fly",
        "vercel",
        "netlify",
        "render",
        "railway",
        "heroku_app_json",
        "cloudflare_wrangler",
        "serverless",
        "k8s",
        "helm",
        "kustomize",
    ]
    file: str
    summary: str | None = None  # e.g. "web: gunicorn app:app" or "k8s Deployment/web"
    evidence: list[SourceRef]


class Analysis(_Model):
    schema_version: Literal["1"] = "1"
    rtfmup_version: str
    root: str  # absolute path
    git_head: str | None
    git_dirty: bool | None
    readme: str | None  # repo-relative path of primary README
    readme_format: Literal["markdown", "rst"] | None
    components: list[Component]
    runtimes: list[Runtime]
    package_managers: list[PackageManager]
    manifests: list[ManifestFinding]
    commands: list[Command]
    env: list[EnvVar]
    services: list[Service]
    external_accounts: list[ExternalAccount]
    ports: list[Port]
    deployments: list[Deployment]
    has_devcontainer: bool
    has_docker: bool
    conflicts: list[Conflict]
    ambiguities: list[Ambiguity]
    warnings: list[str]
