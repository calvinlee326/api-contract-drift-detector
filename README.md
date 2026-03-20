# API Contract Drift Detector

A CLI tool that diffs two OpenAPI/Swagger JSON specs and classifies every change as **breaking**, **non-breaking**, or a **warning** — so microservice teams can catch API regressions before they hit production.

## The Problem

When a backend team ships a new API version, consumers (mobile apps, other services, third-party integrations) silently break if:
- A response field they rely on disappears
- A parameter type changes under them
- A new **required** parameter is added without notice

Manual spec reviews miss these. This tool catches them automatically.

## Demo

```
============================================================
  API CONTRACT DRIFT REPORT
============================================================

  6 breaking  |  2 non-breaking  |  0 warning(s)

GLOBAL
----------------------------------------
  ✗ Endpoint removed: GET /pets/{id}/vaccinations
  ✓ Endpoint added: GET /pets/{id}/notes

GET /pets
----------------------------------------
  ✗ [response]: field removed — 'items.properties.status'
  ✓ [response]: field added — 'items.properties.createdAt'
  ✗ new required parameter 'filter' added

GET /pets/{id}
----------------------------------------
  ✗ [response]: field removed — 'properties.owner'
  ✗ parameter 'id' type changed 'integer' → 'string'

POST /pets
----------------------------------------
  ✗ [request body]: 'properties.breed.type' changed 'string' → 'integer'

============================================================
  Result: BREAKING — review before deploying.
============================================================
```

## Install

```bash
git clone https://github.com/calvinlee326/api-contract-drift-detector.git
cd api-contract-drift-detector
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Basic diff (JSON or YAML)
python detector.py old.json new.json
python detector.py v1.yaml v2.yaml

# Point at a live service URL
python detector.py https://api.example.com/openapi.json v2.json

# Machine-readable JSON output (for CI dashboards, Slack bots, etc.)
python detector.py v1.json v2.json --output json

# CI mode — exits with code 1 if breaking changes are found
python detector.py v1.json v2.json --exit-code

# Plain output (no ANSI color, good for log files)
python detector.py v1.json v2.json --no-color
```

Try it immediately with the included samples:

```bash
python detector.py samples/v1.json samples/v2.json

# Test nested $ref resolution
python detector.py samples/refs_v1.json samples/refs_v2.json
```

## What It Detects

| Change | Classification |
|--------|---------------|
| Endpoint removed | **Breaking** |
| Response field removed | **Breaking** |
| Response/request field type changed | **Breaking** |
| Schema constraint tightened (`type`, `format`, `enum`, `pattern`, min/max) | **Breaking** |
| Parameter removed | **Breaking** |
| New required parameter added | **Breaking** |
| Optional parameter became required | **Breaking** |
| Parameter type changed | **Breaking** |
| Endpoint marked `deprecated: true` | Warning |
| Endpoint added | Non-breaking |
| Response field added | Non-breaking |
| New optional parameter added | Non-breaking |
| Error response code added/removed | Non-breaking |

Changes inside nested `$ref` schemas are fully resolved before diffing — circular refs are handled safely.

## JSON Output

Use `--output json` to get a structured result for scripting:

```bash
python detector.py v1.json v2.json --output json
```

```json
{
  "summary": {
    "breaking": 6,
    "non_breaking": 2,
    "warnings": 0
  },
  "changes": [
    { "severity": "BREAKING", "message": "Endpoint removed: GET /pets/{id}/vaccinations" },
    { "severity": "non-breaking", "message": "Endpoint added: GET /pets/{id}/notes" }
  ]
}
```

## CI Integration

Add to your pipeline to block deployments with breaking changes:

```yaml
# GitHub Actions example
- name: Check API contract drift
  run: python detector.py specs/v1.json specs/v2.json --exit-code
```

The process exits `0` (safe) or `1` (breaking changes found).

## Supported Formats

- OpenAPI 3.x (`requestBody`, `content`, `$ref`)
- Swagger 2.x (body parameters, inline schemas)
- JSON and YAML
- Local file paths or remote URLs

## Stack

- Python 3.x
- [deepdiff](https://github.com/seperman/deepdiff) for recursive schema comparison
- [pyyaml](https://pyyaml.org/) for YAML support
