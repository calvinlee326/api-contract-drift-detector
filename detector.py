#!/usr/bin/env python3
"""
API Contract Drift Detector
Compares two OpenAPI/Swagger JSON specs and reports breaking vs non-breaking changes.
"""

import json
import sys
import argparse
from deepdiff import DeepDiff


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def load_spec(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def get_paths(spec: dict) -> dict:
    """Return the paths object, normalising OpenAPI 2/3."""
    return spec.get("paths", {})


def get_schema_ref(spec: dict, ref: str):
    """Resolve a simple $ref like '#/components/schemas/Foo'."""
    parts = ref.lstrip("#/").split("/")
    node = spec
    for p in parts:
        node = node.get(p, {})
    return node


def resolve(spec: dict, obj: dict) -> dict:
    """Recursively resolve $ref nodes (one level deep is enough for diff)."""
    if "$ref" in obj:
        return get_schema_ref(spec, obj["$ref"])
    return obj


def iter_operations(paths: dict):
    """Yield (path, method, operation_dict) for every operation."""
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method.lower() in http_methods and isinstance(operation, dict):
                yield path, method.lower(), operation


def get_response_schema(spec: dict, operation: dict) -> dict:
    """Extract the schema for the 200 response body (best-effort)."""
    responses = operation.get("responses", {})
    ok = responses.get("200") or responses.get("201") or {}
    ok = resolve(spec, ok)

    # OpenAPI 3
    content = ok.get("content", {})
    for mime in ("application/json", "*/*"):
        if mime in content:
            schema = content[mime].get("schema", {})
            return resolve(spec, schema)

    # Swagger 2
    schema = ok.get("schema", {})
    return resolve(spec, schema)


def get_request_body_schema(spec: dict, operation: dict) -> dict:
    """Extract request body schema (OpenAPI 3 requestBody or Swagger 2 body param)."""
    # OpenAPI 3
    rb = operation.get("requestBody", {})
    rb = resolve(spec, rb)
    content = rb.get("content", {})
    for mime in ("application/json", "*/*"):
        if mime in content:
            schema = content[mime].get("schema", {})
            return resolve(spec, schema)

    # Swagger 2 – body parameter
    for param in operation.get("parameters", []):
        param = resolve(spec, param)
        if param.get("in") == "body":
            return resolve(spec, param.get("schema", {}))

    return {}


def get_query_params(spec: dict, operation: dict) -> dict:
    """Return {name: param_dict} for query/path/header parameters."""
    params = {}
    for param in operation.get("parameters", []):
        param = resolve(spec, param)
        if param.get("in") in ("query", "path", "header"):
            params[param["name"]] = param
    return params


# ──────────────────────────────────────────────
# Change classifiers
# ──────────────────────────────────────────────

BREAKING = "BREAKING"
NON_BREAKING = "non-breaking"


def classify_schema_changes(old_schema: dict, new_schema: dict, context: str, results: list):
    """Diff two JSON schemas and classify each change."""
    if old_schema == new_schema:
        return

    diff = DeepDiff(old_schema, new_schema, ignore_order=True, verbose_level=0)

    # Removed fields → breaking (consumers expect them)
    for path in diff.get("dictionary_item_removed", []):
        field = _human_path(str(path))
        results.append((BREAKING, f"{context}: field removed — '{field}'"))

    # Added fields → non-breaking (consumers can ignore them)
    for path in diff.get("dictionary_item_added", []):
        field = _human_path(str(path))
        results.append((NON_BREAKING, f"{context}: field added — '{field}'"))

    # Type changes → breaking
    for path, change in diff.get("type_changes", {}).items():
        field = _human_path(path)
        results.append((BREAKING,
            f"{context}: type changed at '{field}' "
            f"{change['old_type'].__name__} → {change['new_type'].__name__}"))

    # Value changes (e.g. "type": "string" → "integer") → breaking
    for path, change in diff.get("values_changed", {}).items():
        key = _last_key(path)
        field = _human_path(path)
        if key in ("type", "format", "enum", "pattern", "minimum", "maximum",
                   "minLength", "maxLength", "minItems", "maxItems"):
            results.append((BREAKING,
                f"{context}: '{field}' changed "
                f"{change['old_value']!r} → {change['new_value']!r}"))
        else:
            results.append((NON_BREAKING,
                f"{context}: '{field}' changed "
                f"{change['old_value']!r} → {change['new_value']!r}"))


def _last_key(deepdiff_path: str) -> str:
    """Extract the last key name from a DeepDiff path string like root['foo']['bar']."""
    parts = deepdiff_path.replace("root", "").strip("[]").split("']['")
    return parts[-1].strip("'[]")


def _human_path(deepdiff_path: str) -> str:
    """Convert root['properties']['name']['type'] → properties.name.type"""
    import re
    keys = re.findall(r"\['([^']+)'\]", deepdiff_path)
    return ".".join(keys) if keys else deepdiff_path


# ──────────────────────────────────────────────
# Main diff logic
# ──────────────────────────────────────────────

def diff_specs(old_spec: dict, new_spec: dict) -> list[tuple[str, str]]:
    """
    Returns a list of (severity, description) tuples.
    severity is BREAKING or NON_BREAKING.
    """
    results = []

    old_paths = get_paths(old_spec)
    new_paths = get_paths(new_spec)

    old_ops = {(p, m): op for p, m, op in iter_operations(old_paths)}
    new_ops = {(p, m): op for p, m, op in iter_operations(new_paths)}

    # Removed endpoints
    for key in old_ops:
        if key not in new_ops:
            results.append((BREAKING, f"Endpoint removed: {key[1].upper()} {key[0]}"))

    # Added endpoints
    for key in new_ops:
        if key not in old_ops:
            results.append((NON_BREAKING, f"Endpoint added: {key[1].upper()} {key[0]}"))

    # Changed endpoints
    for key in old_ops:
        if key not in new_ops:
            continue
        path, method = key
        ctx_base = f"{method.upper()} {path}"
        old_op = old_ops[key]
        new_op = new_ops[key]

        # ── Response schema ──
        old_resp = get_response_schema(old_spec, old_op)
        new_resp = get_response_schema(new_spec, new_op)
        classify_schema_changes(old_resp, new_resp, f"{ctx_base} [response]", results)

        # ── Request body schema ──
        old_req = get_request_body_schema(old_spec, old_op)
        new_req = get_request_body_schema(new_spec, new_op)
        classify_schema_changes(old_req, new_req, f"{ctx_base} [request body]", results)

        # ── Query / path / header params ──
        old_params = get_query_params(old_spec, old_op)
        new_params = get_query_params(new_spec, new_op)

        # Removed params
        for name, param in old_params.items():
            if name not in new_params:
                results.append((BREAKING, f"{ctx_base}: parameter removed — '{name}'"))

        # Added params
        for name, param in new_params.items():
            if name not in old_params:
                severity = BREAKING if param.get("required") else NON_BREAKING
                label = "required" if param.get("required") else "optional"
                results.append((severity,
                    f"{ctx_base}: new {label} parameter '{name}' added"))

        # Changed params
        for name in old_params:
            if name not in new_params:
                continue
            old_p = old_params[name]
            new_p = new_params[name]

            # required flip
            was_required = old_p.get("required", False)
            now_required = new_p.get("required", False)
            if not was_required and now_required:
                results.append((BREAKING,
                    f"{ctx_base}: parameter '{name}' became required"))
            elif was_required and not now_required:
                results.append((NON_BREAKING,
                    f"{ctx_base}: parameter '{name}' is no longer required"))

            # type change
            old_type = old_p.get("schema", old_p).get("type")
            new_type = new_p.get("schema", new_p).get("type")
            if old_type and new_type and old_type != new_type:
                results.append((BREAKING,
                    f"{ctx_base}: parameter '{name}' type changed {old_type!r} → {new_type!r}"))

    return results


# ──────────────────────────────────────────────
# CLI rendering
# ──────────────────────────────────────────────

RED   = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD  = "\033[1m"
RESET = "\033[0m"


def _group_by_endpoint(results: list[tuple[str, str]]) -> dict:
    """
    Group results by endpoint label.
    Endpoint-level messages (e.g. 'Endpoint removed') get key '_global'.
    Operation messages (e.g. 'GET /pets [response]: ...') are grouped by 'METHOD /path'.
    """
    import re
    groups = {}
    for severity, msg in results:
        # Match messages that start with an HTTP method + path
        m = re.match(r"^((?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^\s:\[]+)", msg)
        key = m.group(1) if m else "_global"
        groups.setdefault(key, []).append((severity, msg))
    return groups


def print_report(results: list[tuple[str, str]], use_color: bool = True):
    breaking = [r for r in results if r[0] == BREAKING]
    non_breaking = [r for r in results if r[0] == NON_BREAKING]

    def c(text, color):
        return f"{color}{text}{RESET}" if use_color else text

    print()
    print(c("=" * 60, BOLD))
    print(c("  API CONTRACT DRIFT REPORT", BOLD))
    print(c("=" * 60, BOLD))
    print()

    if not results:
        print(c("  No changes detected. Specs are identical.", GREEN))
        print()
        return

    print(c(f"  {len(breaking)} breaking  |  {len(non_breaking)} non-breaking", BOLD))
    print()

    groups = _group_by_endpoint(results)

    # Global messages first (endpoint added/removed)
    global_items = groups.pop("_global", [])
    if global_items:
        print(c("GLOBAL", BOLD))
        print(c("-" * 40, BOLD))
        for severity, msg in global_items:
            icon = "✗" if severity == BREAKING else "✓"
            color = RED if severity == BREAKING else GREEN
            print(c(f"  {icon} {msg}", color))
        print()

    # Per-endpoint groups
    for endpoint, items in sorted(groups.items()):
        has_breaking = any(s == BREAKING for s, _ in items)
        header_color = RED if has_breaking else GREEN
        print(c(endpoint, header_color + BOLD))
        print(c("-" * 40, header_color))
        for severity, msg in items:
            # Strip the endpoint prefix from the message for cleaner display
            detail = msg[len(endpoint):].lstrip(": ")
            icon = "✗" if severity == BREAKING else "✓"
            color = RED if severity == BREAKING else GREEN
            print(c(f"  {icon} {detail}", color))
        print()

    print(c("=" * 60, BOLD))
    if breaking:
        print(c("  Result: BREAKING — review before deploying.", RED))
    else:
        print(c("  Result: Safe to deploy (non-breaking only).", GREEN))
    print(c("=" * 60, BOLD))
    print()


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Detect API contract drift between two OpenAPI/Swagger JSON specs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python detector.py old.json new.json
  python detector.py v1.json v2.json --no-color
  python detector.py v1.json v2.json --exit-code
        """,
    )
    parser.add_argument("old", help="Path to the OLD spec (baseline)")
    parser.add_argument("new", help="Path to the NEW spec (candidate)")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit with code 1 if breaking changes are found (useful in CI)",
    )
    args = parser.parse_args()

    old_spec = load_spec(args.old)
    new_spec = load_spec(args.new)

    results = diff_specs(old_spec, new_spec)
    print_report(results, use_color=not args.no_color)

    if args.exit_code:
        has_breaking = any(r[0] == BREAKING for r in results)
        sys.exit(1 if has_breaking else 0)


if __name__ == "__main__":
    main()
