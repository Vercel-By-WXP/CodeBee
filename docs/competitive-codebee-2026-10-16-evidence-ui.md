# CodeBee evidence visibility — 2026-10-16

## Competitor evidence

- REA `v6.5.0`, released 2026-10-14 and dated 2026-10-13: https://github.com/morluto/rea/releases
- REA README describes local analysis results as including evidence and limitations: https://github.com/morluto/rea

REA is a reverse-engineering product rather than a general coding workbench, so its analysis tools are out of scope. Its recent evidence UX is relevant: compact summaries and retained/paged evidence preserve original coverage, provenance and unknowns instead of implying more certainty than the tool recorded.

## CodeBee comparison and change

CodeBee already normalizes structured task evidence in `app/core/contracts.py` (`coverage`, `unknown`, `limitations`, and `provenance`). The task detail contract overview only rendered criterion status, leaving those fields invisible. The overview now renders an escaped evidence detail card with summary, coverage/scope, source/run/step provenance, unknowns, limitations and an untrusted-content marker. Existing criteria and approval controls remain intact.

## Verification

- `node tests/ui_contract_evidence.mjs` — isolated browser regression for evidence visibility and HTML escaping.
- `node tests/check_ui_syntax.mjs` — frontend syntax checks.
- `cd app; python -m unittest discover -s tests -p test_task_contracts.py` — contract normalization regression.
- `git diff --check`.

## Limits and follow-up

Evidence details currently render inline in the task contract overview. Large evidence sets are not paginated in the UI; current contract storage caps the total evidence list. Next, consider a compact/collapsible details region if real task records make this panel too tall.
