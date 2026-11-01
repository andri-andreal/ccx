---
name: testing
description: Test discipline for builders and reviewers
---
- Every behaviour change has a test that fails without it.
- Prefer table-driven or parameterised tests for input variations.
- Cover the error paths the task mentions, not only the happy path.
- Tests must not depend on network, wall-clock time or test order.
- Exercise HTTP endpoints with `curl -i` so status, headers and body are all shown.
