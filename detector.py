#!/usr/bin/env python3
"""
API Contract Drift Detector
Compares two OpenAPI/Swagger JSON specs and reports breaking vs non-breaking changes.
"""

import json
import re
import sys
import argparse
import urllib.request
from deepdiff import DeepDiff

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def load_spec(source: str) -> dict:
    """Load a spec from a file path (JSON or YAML) or a URL."""
    if source.startswith("http://") or source.startswith("https://"):
        with urllib.request.urlopen(source) as resp:
            raw = resp.read().decode()
        content_type = resp.headers.get("Content-Type", "")
        if "yaml" in content_type or source.endswith((".yaml", ".yml")):
            return _parse_yaml(raw, source)
        return json.loads(raw)

    with open(source) as f:
        raw = f.read()

    if source.endswith((".yaml", ".yml")):
        return _parse_yaml(raw, source)
    return json.loads(raw)


def _parse_yaml(raw: str, source: str) -> dict:
    if not YAML_AVAILABLE:
        print(f"Error: '{source}' looks like YAML but pyyaml is not installed.")
        print("Run: pip install pyyaml")
        sys.exit(1)
    return yaml.safe_load(raw)


def get_paths(spec: dict) -> dict:
    return spec.get("paths", {})


def get_schema_ref(spec: dict, ref: str) -> dict:
    """Resolve a $ref like '#/components/schemas/Foo' by walking the spec."""
    parts = ref.lstrip("#/").split("/")
    node = spec
    for p in parts:
        node = node.get(p, {})
    return node


def resolve(spec: dict, obj: dict, _seen: set | None = None) -> dict:
    """Resolve a single $ref, following chains but guarding against circular refs."""
    if not isinstance(obj, dict):
        return obj
    if "$ref" not in obj:
        return obj
    if _seen is None:
        _seen = set()
    ref = obj["$ref"]
    if ref in _seen:
        return {}  # circular — stop
    _seen.add(ref)
    resolved = get_schema_ref(spec, ref)
    return resolve(spec, resolved, _seen)


def expand(spec: dict, obj: dict, _seen: set | None = None) -> dict:
    """Deeply expand all $ref nodes in a schema tree so deepdiff sees real values."""
    if not isinstance(obj, dict):
        return obj
    if _seen is None:
        _seen = set()

    # Resolve the top-level ref first
    if "$ref" in obj:
        ref = obj["$ref"]
        if ref in _seen:
            return {}
        _seen = _seen | {ref}
        obj = get_schema_ref(spec, ref)
        if not isinstance(obj, dict):
            return obj

    # Recursively expand every value in the dict
    return {k: expand(spec, v, _seen) for k, v in obj.items()}


def iter_operations(paths: dict):
    """Yield (path, method, operation_dict) for every operation."""
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method.lower() in http_methods and isinstance(operation, dict):
                yield path, method.lower(), operation


def get_response_schema(spec: dict, operation: dict) -> dict:
    """Extract the schema for the success response body (best-effort)."""
    responses = operation.get("responses", {})
    ok = resolve(spec, responses.get("200") or responses.get("201") or {})

    # OpenAPI 3
    content = ok.get("content", {})
    for mime in ("application/json", "*/*"):
        if mime in content:
            return expand(spec, content[mime].get("schema", {}))

    # Swagger 2
    return expand(spec, ok.get("schema", {}))


def get_request_body_schema(spec: dict, operation: dict) -> dict:
    """Extract request body schema (OpenAPI 3 requestBody or Swagger 2 body param)."""
    rb = resolve(spec, operation.get("requestBody", {}))
    content = rb.get("content", {})
    for mime in ("application/json", "*/*"):
        if mime in content:
            return expand(spec, content[mime].get("schema", {}))

    for param in operation.get("parameters", []):
        param = resolve(spec, param)
        if param.get("in") == "body":
            return expand(spec, param.get("schema", {}))

    return {}


def get_query_params(spec: dict, operation: dict) -> dict:
    """Return {name: param_dict} for query/path/header parameters."""
    params = {}
    for param in operation.get("parameters", []):
        param = resolve(spec, param)
        if param.get("in") in ("query", "path", "header"):
            params[param["name"]] = param
    return params


def get_response_codes(operation: dict) -> set:
    """Return the set of defined response status codes."""
    return set(str(k) for k in operation.get("responses", {}).keys())


# ──────────────────────────────────────────────
# Change classifiers
# ──────────────────────────────────────────────

BREAKING     = "BREAKING"
NON_BREAKING = "non-breaking"
WARNING      = "warning"


def _last_key(deepdiff_path: str) -> str:
    parts = deepdiff_path.replace("root", "").strip("[]").split("']['")
    return parts[-1].strip("'[]")


def _human_path(deepdiff_path: str) -> str:
    """Convert root['properties']['name']['type'] → properties.name.type"""
    keys = re.findall(r"\['([^']+)'\]", deepdiff_path)
    return ".".join(keys) if keys else deepdiff_path


def classify_schema_changes(old_schema: dict, new_schema: dict, context: str, results: list):
    """Diff two JSON schemas and classify each change."""
    if old_schema == new_schema:
        return

    diff = DeepDiff(old_schema, new_schema, ignore_order=True, verbose_level=2)

    for path in diff.get("dictionary_item_removed", []):
        field = _human_path(str(path))
        results.append((BREAKING, f"{context}: field removed — '{field}'"))

    for path in diff.get("dictionary_item_added", []):
        field = _human_path(str(path))
        results.append((NON_BREAKING, f"{context}: field added — '{field}'"))

    for path, change in diff.get("type_changes", {}).items():
        field = _human_path(path)
        results.append((BREAKING,
            f"{context}: type changed at '{field}' "
            f"{change['old_type'].__name__} → {change['new_type'].__name__}"))

    for path, change in diff.get("values_changed", {}).items():
        key   = _last_key(path)
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


def check_deprecated(old_op: dict, new_op: dict, ctx: str, results: list):
    """Warn when an endpoint or parameter gains deprecated: true."""
    was = old_op.get("deprecated", False)
    now = new_op.get("deprecated", False)
    if not was and now:
        results.append((WARNING, f"{ctx}: marked as deprecated"))


def check_response_codes(old_op: dict, new_op: dict, ctx: str, results: list):
    """Detect removed or added response status codes."""
    old_codes = get_response_codes(old_op)
    new_codes = get_response_codes(new_op)

    for code in old_codes - new_codes:
        # Removing a success code is breaking; removing an error code is non-breaking
        severity = BREAKING if code.startswith("2") else NON_BREAKING
        results.append((severity, f"{ctx}: response code {code} removed"))

    for code in new_codes - old_codes:
        severity = NON_BREAKING if code.startswith("2") else NON_BREAKING
        results.append((NON_BREAKING, f"{ctx}: response code {code} added"))


# ──────────────────────────────────────────────
# Main diff logic
# ──────────────────────────────────────────────

def diff_specs(old_spec: dict, new_spec: dict) -> list[tuple[str, str]]:
    results = []

    old_ops = {(p, m): op for p, m, op in iter_operations(get_paths(old_spec))}
    new_ops = {(p, m): op for p, m, op in iter_operations(get_paths(new_spec))}

    # Removed / added endpoints
    for key in old_ops:
        if key not in new_ops:
            results.append((BREAKING, f"Endpoint removed: {key[1].upper()} {key[0]}"))
    for key in new_ops:
        if key not in old_ops:
            results.append((NON_BREAKING, f"Endpoint added: {key[1].upper()} {key[0]}"))

    # Changed endpoints
    for key in old_ops:
        if key not in new_ops:
            continue
        path, method = key
        ctx      = f"{method.upper()} {path}"
        old_op   = old_ops[key]
        new_op   = new_ops[key]

        # ── Deprecated flag ──
        check_deprecated(old_op, new_op, ctx, results)

        # ── Response codes ──
        check_response_codes(old_op, new_op, ctx, results)

        # ── Response schema ──
        classify_schema_changes(
            get_response_schema(old_spec, old_op),
            get_response_schema(new_spec, new_op),
            f"{ctx} [response]", results,
        )

        # ── Request body schema ──
        classify_schema_changes(
            get_request_body_schema(old_spec, old_op),
            get_request_body_schema(new_spec, new_op),
            f"{ctx} [request body]", results,
        )

        # ── Query / path / header params ──
        old_params = get_query_params(old_spec, old_op)
        new_params = get_query_params(new_spec, new_op)

        for name in old_params:
            if name not in new_params:
                results.append((BREAKING, f"{ctx}: parameter removed — '{name}'"))

        for name, param in new_params.items():
            if name not in old_params:
                severity = BREAKING if param.get("required") else NON_BREAKING
                label    = "required" if param.get("required") else "optional"
                results.append((severity, f"{ctx}: new {label} parameter '{name}' added"))

        for name in old_params:
            if name not in new_params:
                continue
            old_p = old_params[name]
            new_p = new_params[name]

            was_required = old_p.get("required", False)
            now_required = new_p.get("required", False)
            if not was_required and now_required:
                results.append((BREAKING, f"{ctx}: parameter '{name}' became required"))
            elif was_required and not now_required:
                results.append((NON_BREAKING, f"{ctx}: parameter '{name}' is no longer required"))

            old_type = old_p.get("schema", old_p).get("type")
            new_type = new_p.get("schema", new_p).get("type")
            if old_type and new_type and old_type != new_type:
                results.append((BREAKING,
                    f"{ctx}: parameter '{name}' type changed {old_type!r} → {new_type!r}"))

    return results


# ──────────────────────────────────────────────
# CLI rendering
# ──────────────────────────────────────────────

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def _group_by_endpoint(results: list[tuple[str, str]]) -> dict:
    groups = {}
    for severity, msg in results:
        m = re.match(r"^((?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^\s:\[]+)", msg)
        key = m.group(1) if m else "_global"
        groups.setdefault(key, []).append((severity, msg))
    return groups


def output_json(results: list[tuple[str, str]]):
    payload = {
        "summary": {
            "breaking":     sum(1 for r in results if r[0] == BREAKING),
            "non_breaking": sum(1 for r in results if r[0] == NON_BREAKING),
            "warnings":     sum(1 for r in results if r[0] == WARNING),
        },
        "changes": [{"severity": s, "message": m} for s, m in results],
    }
    print(json.dumps(payload, indent=2))


def print_report(results: list[tuple[str, str]], use_color: bool = True):
    breaking     = [r for r in results if r[0] == BREAKING]
    non_breaking = [r for r in results if r[0] == NON_BREAKING]
    warnings     = [r for r in results if r[0] == WARNING]

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

    summary = f"  {len(breaking)} breaking  |  {len(non_breaking)} non-breaking  |  {len(warnings)} warning(s)"
    print(c(summary, BOLD))
    print()

    groups = _group_by_endpoint(results)

    global_items = groups.pop("_global", [])
    if global_items:
        print(c("GLOBAL", BOLD))
        print(c("-" * 40, BOLD))
        for severity, msg in global_items:
            icon  = "✗" if severity == BREAKING else ("⚠" if severity == WARNING else "✓")
            color = RED if severity == BREAKING else (YELLOW if severity == WARNING else GREEN)
            print(c(f"  {icon} {msg}", color))
        print()

    for endpoint, items in sorted(groups.items()):
        has_breaking = any(s == BREAKING for s, _ in items)
        has_warning  = any(s == WARNING  for s, _ in items)
        header_color = RED if has_breaking else (YELLOW if has_warning else GREEN)
        print(c(endpoint, header_color + BOLD))
        print(c("-" * 40, header_color))
        for severity, msg in items:
            detail = msg[len(endpoint):].lstrip(": ")
            icon   = "✗" if severity == BREAKING else ("⚠" if severity == WARNING else "✓")
            color  = RED if severity == BREAKING else (YELLOW if severity == WARNING else GREEN)
            print(c(f"  {icon} {detail}", color))
        print()

    print(c("=" * 60, BOLD))
    if breaking:
        print(c("  Result: BREAKING — review before deploying.", RED))
    elif warnings:
        print(c("  Result: No breaking changes, but deprecations present.", YELLOW))
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
  python detector.py v1.yaml v2.yaml
  python detector.py https://api.example.com/v1/openapi.json v2.json
  python detector.py v1.json v2.json --no-color
  python detector.py v1.json v2.json --output json
  python detector.py v1.json v2.json --exit-code
        """,
    )
    parser.add_argument("old", help="Path or URL to the OLD spec (baseline)")
    parser.add_argument("new", help="Path or URL to the NEW spec (candidate)")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format: 'text' (default) or 'json' for machine-readable output",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit with code 1 if breaking changes are found (useful in CI)",
    )
    args = parser.parse_args()

    old_spec = load_spec(args.old)
    new_spec = load_spec(args.new)

    results = diff_specs(old_spec, new_spec)

    if args.output == "json":
        output_json(results)
    else:
        print_report(results, use_color=not args.no_color)

    if args.exit_code:
        sys.exit(1 if any(r[0] == BREAKING for r in results) else 0)


if __name__ == "__main__":
    main()
