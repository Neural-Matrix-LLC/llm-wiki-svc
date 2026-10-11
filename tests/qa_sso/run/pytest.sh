#!/usr/bin/env bash
# Run the suite against the stack started by start_all.sh:  tests/qa_sso/run/pytest.sh [pytest args]
# (from the llm-wiki-svc repo root; uses the QA venv, which has llmwiki installed for tests/conftest.py)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
source "$RUN_DIR/qa.env"
cd "$WIKI_REPO"
export QA_SSO=1
exec "$WIKI_VENV/bin/python" -m pytest tests/qa_sso -p no:cacheprovider -o addopts="" -rA "$@"
