"""Answer-quality evaluation and the correction loop (Phase 1-D, design §4.9).

L4, a peer of ``cli.py``: reaches the domain only through ``llmwiki.tools``.
``langsmith`` is imported function-locally in the two places that talk to it
(``run.run_experiment``, ``dataset.push_dataset``, ``feedback``), so
``import llmwiki.eval`` is free and the offline loop (``run.run_local``)
needs no key and no network - ``tests/unit/test_eval.py`` asserts both.
"""
