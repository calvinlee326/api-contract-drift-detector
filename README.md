# API Contract Drift Detector

A CLI tool that diffs two OpenAPI/Swagger JSON specs and classifies every change as **breaking** or **non-breaking** — so microservice teams can catch API regressions before they hit production.

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

  5 breaking change(s)  |  2 non-breaking change(s)

BREAKING CHANGES (5)
----------------------------------------
  ✗ Endpoint removed: GET /pets/{id}/vaccinations
  ✗ GET /pets [response]: field removed — root['items']['properties']['status']
  ✗ GET /pets: new required parameter 'filter' added
  ✗ GET /pets/{id} [response]: field removed — root['properties']['owner']
  ✗ GET /pets/{id}: parameter 'id' type changed 'integer' → 'string'

NON-BREAKING CHANGES (2)
----------------------------------------
  ✓ Endpoint added: GET /pets/{id}/notes
  ✓ GET /pets [response]: field added — root['items']['properties']['createdAt']

============================================================
  Result: BREAKING — review before deploying.
============================================================
```

## Install

```bash
git clone https://github.com/calvinlee326/api-contract-drift-detector.git
cd api-contract-drift-detector
pip install -r requirements.txt
```

## Usage

```bash
# Basic diff
python detector.py old.json new.json

# CI mode — exits with code 1 if breaking changes are found
python detector.py v1.json v2.json --exit-code

# Plain output (no ANSI color, good for log files)
python detector.py v1.json v2.json --no-color
```

Try it immediately with the included samples:

```bash
python detector.py samples/v1.json samples/v2.json
```

## What It Detects

| Change | Classification |
|--------|---------------|
| Endpoint removed | **Breaking** |
| Response field removed | **Breaking** |
| Response field type changed | **Breaking** |
| Schema constraint tightened (`type`, `format`, `enum`, `pattern`, min/max) | **Breaking** |
| Parameter removed | **Breaking** |
| New required parameter added | **Breaking** |
| Optional parameter became required | **Breaking** |
| Parameter type changed | **Breaking** |
| Endpoint added | Non-breaking |
| Response field added | Non-breaking |
| New optional parameter added | Non-breaking |

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

## Stack

- Python 3.x
- [deepdiff](https://github.com/seperman/deepdiff) for recursive JSON comparison
