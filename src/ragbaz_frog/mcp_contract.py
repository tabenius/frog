"""Language-neutral tool contract; Python and Dash consume the same JSON bytes."""
from __future__ import annotations

import copy
import json
import uuid
from importlib.resources import files


def contract() -> dict:
    return json.loads(files("ragbaz_frog").joinpath("mcp_contract.json").read_text())


def shared_specs() -> list[dict]:
    catalog = contract()
    return [dict(copy.deepcopy(tool), annotations=dict(catalog["readAnnotations"]))
            for tool in catalog["tools"]]


def validate_arguments(name: str, args: object) -> None:
    """Validate the small JSON Schema vocabulary used by the shared contract.

    Kept dependency-free for Frog's legacy stdio path. Contract tests refuse
    schema vocabulary outside this subset, rather than silently ignoring it.
    The SDK also validates the same JSON Schema on its official transport.
    """
    spec = next((tool for tool in shared_specs() if tool["name"] == name), None)
    if spec is None:
        return
    schema = spec["inputSchema"]
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    if set(args) - set(schema["properties"]):
        raise ValueError("unknown arguments")
    if set(schema.get("required", [])) - set(args):
        raise ValueError("missing required arguments")
    for key, value in args.items():
        rule = schema["properties"][key]
        if rule["type"] == "string" and not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        if rule["type"] == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{key} must be an integer")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"unsupported {key}")
        if "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"{key} is too small")
        if "maximum" in rule and value > rule["maximum"]:
            raise ValueError(f"{key} is too large")
        if rule.get("format") == "uuid":
            parsed = uuid.UUID(value)
            if str(parsed) != value.lower() or (parsed.int not in (0, (1 << 128) - 1) and parsed.version not in range(1, 9)):
                raise ValueError(f"invalid {key}")
