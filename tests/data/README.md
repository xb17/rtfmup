# Vendored test data

`devContainer.base.schema.json`: the official dev container JSON schema.

- Source: https://raw.githubusercontent.com/devcontainers/spec/main/schemas/devContainer.base.schema.json
- Fetched: 2026-11-08
- Draft: 2019-09
- Used by `tests/test_devcontainer.py` through `tests/mini_jsonschema.py`, a small
  test-only validator (no `jsonschema` dependency; PLAN D20). The validator raises on
  any keyword it does not implement, so refreshing this file can't silently weaken
  the check.
