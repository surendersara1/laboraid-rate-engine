# ProfileDrafterAgent — Self-Audit Report

*Run at 2026-06-05T04:11:41.748805+00:00*

## Summary

- **Total checks:** 31
- **Passed:** 31
- **Failed:** 0
- **Skipped:** 0

**Verdict:** all gates green. Ready to merge.

## Check results

| Check | Status | Detail |
|---|---|---|
| drafter directory exists | PASS | E:\NBS_LaborAid\laboraid-rate-engine\agents\profile_drafter |
| file: Dockerfile | PASS | 1891 bytes |
| file: pyproject.toml | PASS | 1314 bytes |
| file: system-prompt.md | PASS | 5519 bytes |
| file: agent.py | PASS | 5638 bytes |
| file: steering.py | PASS | 2385 bytes |
| file: schema_check.py | PASS | 8933 bytes |
| file: codegen_check.py | PASS | 7499 bytes |
| file: orchestrate.py | PASS | 6063 bytes |
| file: commit_helper.py | PASS | 7080 bytes |
| file: tests/test_system_prompt.py | PASS | 1954 bytes |
| file: tests/test_schema_check.py | PASS | 4950 bytes |
| file: tests/test_codegen_check.py | PASS | 3925 bytes |
| file: tests/test_agent.py | PASS | 4600 bytes |
| file: tests/test_orchestrate_smoke.py | PASS | 6154 bytes |
| file: tests/test_analyze_groundtruth.py | PASS | 5227 bytes |
| agent.py contract | PASS | all required patterns present |
| tool defs present | PASS | all 5 @tool functions defined |
| tools registered in agent | PASS | all 5 referenced in source |
| steering.py contract | PASS | Guide/Proceed pattern present |
| system-prompt contract | PASS | never-fabricate + RFC-2119 keywords |
| uv sync (drafter) | PASS |  |
| pytest (drafter) | PASS | ........................................................................ [ 82%] \| ...............                                                          [100%] \| 87 passed in 0.57s \| warning: `VI |
| mypy --strict (drafter) | PASS |  |
| ruff check (drafter) | PASS |  |
| black --check (drafter) | PASS |  |
| on feat branch | PASS | feat/path-c-and-drafter |
| [DRAFT-*] commits present | PASS | 14 drafter commits found |
| kernel/ untouched by drafter SOURCE | PASS | no kernel/ files modified in drafter commits |
| no static AWS/Anthropic creds in repo | PASS |  |
| BUILD_LOG.md updated | PASS | 29 drafter entries |