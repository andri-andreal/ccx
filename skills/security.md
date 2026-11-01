---
name: security
description: Security checks for reviewers
---
- Untrusted input is validated before it reaches queries, paths, shells or templates.
- Queries are parameterised; no string-built SQL or shell commands.
- Secrets never appear in code, logs, error messages or test fixtures.
- Errors returned to clients do not leak internals such as stack traces or SQL.
