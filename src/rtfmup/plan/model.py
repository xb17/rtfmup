"""Plan models (PLAN §2.3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from rtfmup.models import Port, SourceRef

StepKind = Literal[
    "runtime", "install", "env", "setup", "build", "run", "healthcheck", "manual"
]
Risk = Literal["low", "medium", "high"]
GapKind = Literal[
    "missing_runtime",
    "runtime_version",
    "missing_env",
    "external_account",
    "unsupported_platform",
    "service",
    "working_directory",
    "unresolved",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Step(_Model):
    id: str
    kind: StepKind
    title: str
    argv: list[str] | None = None  # set when runnable as argv (D17)
    shell_text: str | None = None  # set when needs_shell; executed via pwsh
    cwd: str = "."  # repo-relative, forward slashes
    env: dict[str, str] = {}  # NEVER contains secret values
    risk: Risk = "low"
    risk_reasons: list[str] = []
    platform_ok: bool = True  # False: listed, not run (D7)
    from_commands: list[str] = []  # Command.id values ([] if manifest-derived)
    evidence: list[SourceRef] = []
    long_running: bool = False  # run steps stay in the foreground


class Gap(_Model):
    kind: GapKind
    message: str
    fix: str | None = None


class Plan(_Model):
    schema_version: Literal["1"] = "1"
    analysis_ref: str  # sha256 of the analysis.json bytes
    host_os: Literal["windows", "linux", "macos"]
    host_arch: Literal["x86_64", "arm64"]
    variant: str | None
    component: str
    steps: list[Step]
    gaps: list[Gap]
    health: Port | None
