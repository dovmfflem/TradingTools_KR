# Agent Instructions

## Test Result Artifacts

All test workflows added to this repository must save their results for future reference.

- Public exchange API tests must save request metadata and response samples.
- Private/authenticated API tests should avoid saving sensitive fields by default. When the user explicitly requests real local values for debugging, save unmasked results only under ignored local result directories such as `tests/results/`.
- Saved artifacts should include the exchange name, API category, API name, HTTP method, endpoint, request parameters, capture time, success/failure state, and response or error summary.
- Prefer stable, reviewable locations such as `tests/fixtures/<exchange>/<category>/` for reusable sample responses and `tests/results/<exchange>/<category>/` for timestamped run logs.
- Keep large or volatile responses trimmed when a short sample is enough for future implementation work.
- Do not commit real credentials, account identifiers, private keys, bearer tokens, addresses, transaction IDs, or other sensitive values.

When creating or updating a manual/live test script, add a result-saving option by default or make the script save results automatically unless there is a clear reason not to.
