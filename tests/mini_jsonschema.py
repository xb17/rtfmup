"""A deliberately small JSON Schema validator (draft 2019-09 subset), test-only.

We validate devcontainer.json against the official schema without adding a
dependency (PLAN D20, decided by Drew: no `jsonschema`). It supports exactly the
keywords the vendored schema uses and RAISES on any other validating keyword it
meets, so a schema update that needs more cannot silently pass.

Supported: $ref (local `#/...`), type, enum, const, pattern, minimum, maximum,
required, properties, patternProperties, additionalProperties, items, minItems,
oneOf, anyOf, allOf, unevaluatedProperties.
Annotation-only keywords (description, default, examples, ...) are ignored.
"""

from __future__ import annotations

import re
from typing import Any

ANNOTATIONS = {
    "$schema",
    "$comment",
    "description",
    "markdownDescription",
    "default",
    "examples",
    "deprecated",
    "deprecationMessage",
    "enumDescriptions",
    "enumItemLabels",
    "defaultSnippets",
    "title",
    "label",
    "format",
    "allowComments",
    "allowTrailingCommas",
    "definitions",
    "$defs",
}
SUPPORTED = {
    "$ref",
    "type",
    "enum",
    "const",
    "pattern",
    "minimum",
    "maximum",
    "required",
    "properties",
    "patternProperties",
    "additionalProperties",
    "items",
    "minItems",
    "oneOf",
    "anyOf",
    "allOf",
    "unevaluatedProperties",
}
TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name in ("string", "array", "object", "null"):
        return isinstance(value, TYPES[name])
    if name == "boolean":
        return isinstance(value, bool)
    raise NotImplementedError(f"type {name!r}")


class Validator:
    def __init__(self, root: dict):
        self.root = root

    def errors(self, instance: Any) -> list[str]:
        return self._validate(instance, self.root, "$")[0]

    # Returns (errors, evaluated property names) for object instances.
    def _validate(
        self, inst: Any, schema: Any, path: str
    ) -> tuple[list[str], set[str]]:
        if schema is True:
            return [], set(inst) if isinstance(inst, dict) else set()
        if schema is False:
            return [f"{path}: not allowed"], set()
        unknown = set(schema) - SUPPORTED - ANNOTATIONS
        # keys that the vendored schema uses as vendor annotations / property names
        unknown = {k for k in unknown if not self._is_vendor_annotation(k, schema)}
        if unknown:
            raise NotImplementedError(
                f"{path}: unsupported keyword(s) {sorted(unknown)}"
            )

        errs: list[str] = []
        seen: set[str] = set()

        if "$ref" in schema:
            e, s = self._validate(inst, self._resolve(schema["$ref"]), path)
            errs += e
            seen |= s

        if "type" in schema:
            types = (
                schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            )
            if not any(_is_type(inst, t) for t in types):
                errs.append(
                    f"{path}: expected {'|'.join(types)}, got {type(inst).__name__}"
                )
                return errs, seen
        if "enum" in schema and inst not in schema["enum"]:
            errs.append(f"{path}: {inst!r} not in {schema['enum']}")
        if "const" in schema and inst != schema["const"]:
            errs.append(f"{path}: expected {schema['const']!r}")
        pattern = schema.get("pattern")
        if isinstance(inst, str) and pattern and not re.search(pattern, inst):
            errs.append(f"{path}: {inst!r} does not match {pattern!r}")
        if isinstance(inst, (int, float)) and not isinstance(inst, bool):
            if "minimum" in schema and inst < schema["minimum"]:
                errs.append(f"{path}: {inst} < {schema['minimum']}")
            if "maximum" in schema and inst > schema["maximum"]:
                errs.append(f"{path}: {inst} > {schema['maximum']}")

        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                errs.append(f"{path}: fewer than {schema['minItems']} items")
            if "items" in schema:
                for i, item in enumerate(inst):
                    errs += self._validate(item, schema["items"], f"{path}[{i}]")[0]

        if isinstance(inst, dict):
            for name in schema.get("required", []):
                if name not in inst:
                    errs.append(f"{path}: missing required property {name!r}")
            props = schema.get("properties", {})
            patterns = schema.get("patternProperties", {})
            for key, value in inst.items():
                matched = False
                if key in props:
                    matched = True
                    errs += self._validate(value, props[key], f"{path}.{key}")[0]
                for rx, sub in patterns.items():
                    if re.search(rx, key):
                        matched = True
                        errs += self._validate(value, sub, f"{path}.{key}")[0]
                if matched:
                    seen.add(key)
            if "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                for key, value in inst.items():
                    if key in props or any(re.search(rx, key) for rx in patterns):
                        continue
                    seen.add(key)
                    if extra is False:
                        errs.append(f"{path}: unexpected property {key!r}")
                    elif extra is not True:
                        errs += self._validate(value, extra, f"{path}.{key}")[0]

        for kw in ("allOf",):
            for sub in schema.get(kw, []):
                e, s = self._validate(inst, sub, path)
                errs += e
                seen |= s
        if "anyOf" in schema:
            results = [self._validate(inst, s, path) for s in schema["anyOf"]]
            ok = [r for r in results if not r[0]]
            if not ok:
                errs.append(f"{path}: matches none of anyOf")
            for _, s in ok:
                seen |= s
        if "oneOf" in schema:
            results = [self._validate(inst, s, path) for s in schema["oneOf"]]
            ok = [r for r in results if not r[0]]
            if len(ok) != 1:
                detail = "; ".join(r[0][0] for r in results if r[0])[:300]
                errs.append(f"{path}: matches {len(ok)} of oneOf (need 1) [{detail}]")
            for _, s in ok:
                seen |= s

        if "unevaluatedProperties" in schema and isinstance(inst, dict):
            rule = schema["unevaluatedProperties"]
            for key, value in inst.items():
                if key in seen:
                    continue
                if rule is False:
                    errs.append(f"{path}: unevaluated property {key!r}")
                elif rule is not True:
                    errs += self._validate(value, rule, f"{path}.{key}")[0]
            seen |= set(inst)
        return errs, seen

    @staticmethod
    def _is_vendor_annotation(key: str, schema: dict) -> bool:
        # The devcontainer schema carries editor hints such as onAutoForward inside
        # property *values*; those are never schema keywords at this level.
        return key in {
            "body",
            "elevateIfNeeded",
            "requireLocalPort",
            "protocol",
            "label",
        }

    def _resolve(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            raise NotImplementedError(f"non-local $ref {ref!r}")
        node: Any = self.root
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node
