# Gates: handler-census-w2b-gpu — seeded from MAP.md checks

Scope: every answer-key check, as a gate

- [x] G1: Q1: What modules and handlers are in scope for HANDLER-CENSUS-W2B-GPU? — python -c "import sys; sys.path.insert(0, 'tests/unit'); from test_handler_dispositions import _real_handlers_for_module; assert sum(len(_real_handlers_for_module(m)) for m in ['backends/cudf_backend.py', 'backends/torch_backend.py', 'backends/cybert_backend.py']) == 21; print('21 handlers in scope')"
  CHECK: python -c "import sys; sys.path.insert(0, 'tests/unit'); from test_handler_dispositions import _real_handlers_for_module; assert sum(len(_real_handlers_for_module(m)) for m in ['backends/cudf_backend.py', 'backends/torch_backend.py', 'backends/cybert_backend.py']) == 21; print('21 handlers in scope')"
  EXPECT: 21 handlers in scope
  EVIDENCE: 21 handlers in scope

- [x] G2: Q2: What are the dispositions and categories for each of the 21 handlers? — uv run --no-sync python -m pytest tests/unit/test_handler_dispositions.py -q
  CHECK: uv run --no-sync python -m pytest tests/unit/test_handler_dispositions.py -q
  EXPECT: passed
  EVIDENCE: ...........                                                              [100%] | 11 passed (0:03:38)

- [x] G3: Q3: How is the completeness ratchet updated without breaking preexisting gates? — uv run --no-sync python -m pytest tests/unit/test_silent_failure_hardening.py -q
  CHECK: uv run --no-sync python -m pytest tests/unit/test_silent_failure_hardening.py -q
  EXPECT: passed
  EVIDENCE: ..                                                                       [100%] | 2 passed

<!-- seeded by build_state.py --seed-gates; edit CHECK/EXPECT, never delete a G<n> — ABANDON it -->
