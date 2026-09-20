---
layout: ../../layouts/Docs.astro
title: Diagnostics
description: Validate ccx profiles and certify provider capabilities safely.
---

## Doctor: setup and reachability

```bash
ccx doctor                         # local installation summary
ccx doctor work                    # profile + authenticated reachability
ccx doctor work --no-network       # no provider connection
ccx doctor work --json             # stable machine-readable report
```

Doctor checks the Claude and router binaries, profile name/syntax, private file and
isolation permissions, provider/model, endpoint security, credential presence,
fallback/key alignment, retry policy, provider pinning, and an authenticated
model-list endpoint.
It never sends a prompt, so the check itself does not consume model tokens. A remote
plain-HTTP endpoint is rejected; HTTP is allowed for actual loopback development.

The `router.provider_pinning` check reports the configured OpenRouter pinning and
fails when it describes more upstreams than the profile has. It warns, rather than
fails, when pinning is configured but no upstream is `openrouter.ai`: the
`provider` field is then ignored, unless the endpoint proxies OpenRouter, which is
a legitimate setup ccx cannot detect from the URL alone.

Exit status is `0` when no required check fails and `1` otherwise. Warnings and
skipped optional checks do not fail the command.

## Certify: active capability probes

```bash
ccx certify work
ccx certify work --checks basic,streaming
ccx certify work --checks all --yes --json
```

Certification sends at most one minimal request for each selected capability:

- `basic`: a one-token response probe (required)
- `streaming`: a one-token SSE response probe
- `tools`: a forced call to an inert `ccx_probe` tool; no external action is run

Every selected capability is required for a successful certification exit status;
unselected capabilities are reported as skipped.

The interactive command asks first. Non-interactive callers must pass `--yes`;
without it, valid JSON is still returned but the consent check fails and no network
request is made. Provider charges and rate limits can apply. Certification uses
Python 3 or Node.js to parse response JSON/SSE structurally; it fails closed when
neither parser is available.

For a routed profile, these probes target the configured OpenAI-compatible upstream.
They do not claim to certify the translation hop; that behavior is exercised by the
router's conformance tests.

## JSON contract

The current contract is versioned as `schema_version: 1`:

```json
{
  "schema_version": 1,
  "command": "doctor",
  "profile": "work",
  "status": "pass",
  "summary": { "passed": 8, "warnings": 0, "failed": 0, "skipped": 1 },
  "checks": [
    {
      "id": "profile.permissions",
      "label": "File permissions",
      "status": "pass",
      "message": "Profile file is private (0600).",
      "required": true
    }
  ],
  "capabilities": {
    "basic": "unknown",
    "streaming": "unknown",
    "tools": "unknown"
  }
}
```

Check status is `pass`, `warn`, `fail`, or `skip`; capability state may also be
`unknown` before certification. IDs and schema version are intended for automation.
Messages are human-readable and secret-redacted.

## Desktop behavior

The GUI runs `doctor --no-network` automatically for each profile. Expanding a card
shows every check, while **Doctor** adds network reachability and **Certify** opens an
OS-native cost/consent dialog in the Rust backend before running probes. Failed
reports are parsed and shown normally rather than being collapsed into a generic
command error.
