"""The test-only validator must actually reject bad devcontainer.json documents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mini_jsonschema import Validator

SCHEMA = json.loads(
    (Path(__file__).parent / "data" / "devContainer.base.schema.json").read_text(
        encoding="utf-8"
    )
)
V = Validator(SCHEMA)

GOOD = [
    {"image": "mcr.microsoft.com/devcontainers/base:ubuntu24.04"},
    {
        "name": "x",
        "image": "i",
        "features": {"ghcr.io/devcontainers/features/node:2": {"version": "20"}},
        "forwardPorts": [3000, "db:5432"],
        "postCreateCommand": "npm ci",
        "containerEnv": {"A": "b"},
        "customizations": {"rtfmup": {"notes": ["anything"]}},
    },
    {"image": "i", "postCreateCommand": ["npm", "ci"]},
    {"image": "i", "postCreateCommand": {"a": "x", "b": ["y", "z"]}},
    {"dockerComposeFile": "../compose.yml", "service": "app", "workspaceFolder": "/w"},
    {
        "dockerComposeFile": ["a.yml", "b.yml"],
        "service": "app",
        "workspaceFolder": "/w",
    },
    {},  # valid: the schema allows common-only documents
]
BAD = [
    ({"image": "i", "bogusKey": 1}, "unknown property"),
    ({"image": 5}, "wrong type"),
    ({"image": "i", "forwardPorts": [70000]}, "port out of range"),
    ({"image": "i", "forwardPorts": "3000"}, "forwardPorts must be an array"),
    ({"image": "i", "postCreateCommand": 5}, "bad command type"),
    ({"image": "i", "features": []}, "features must be an object"),
    (
        {"image": "i", "dockerComposeFile": "c.yml", "service": "app"},
        "two kinds at once",
    ),
    (
        {"dockerComposeFile": "c.yml", "workspaceFolder": "/w"},
        "compose without service",
    ),
    (
        {"dockerComposeFile": "c.yml", "service": "app"},
        "compose without workspaceFolder",
    ),
    ({"image": "i", "shutdownAction": "explode"}, "bad enum"),
    ({"image": "i", "remoteUser": ["a"]}, "bad type"),
]


@pytest.mark.parametrize("doc", GOOD)
def test_accepts_valid_documents(doc):
    assert V.errors(doc) == []


@pytest.mark.parametrize(("doc", "why"), BAD, ids=[w for _, w in BAD])
def test_rejects_invalid_documents(doc, why):
    assert V.errors(doc), why


def test_unsupported_keywords_raise_instead_of_passing():
    with pytest.raises(NotImplementedError):
        Validator({"if": {}}).errors({})
    with pytest.raises(NotImplementedError):
        Validator({"not": {"type": "string"}}).errors("x")
