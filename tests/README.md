# Public validation

The default suite uses synthetic questions, excerpts, identifiers and temporary
databases. It does not require a production profile, downloaded documentation,
QQ credentials, or access to a model service.

```bash
python3 scripts/bootstrap.py --venv .venv --dev
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check . --select E9,F,I
.venv/bin/python scripts/offline_demo.py
.venv/bin/python scripts/check_release.py
```

The actual Hermes dispatcher test is skipped unless `HERMES_CHECKOUT` is set.
After preparing the dedicated checkout as described in the setup guide, install
the test dependencies in that checkout's virtual environment and run:

```bash
export HERMES_CHECKOUT="$HOME/work/hermes-qq"
"$HERMES_CHECKOUT/venv/bin/python" -m pip install -r requirements-dev.txt
"$HERMES_CHECKOUT/venv/bin/python" -m pytest tests/test_hermes_integration.py -q
```

This test loads the exported plugin with the real Hermes plugin loader, invokes
the execution middleware with a synthetic answer, and verifies that provider
failure cannot invoke the generic agent and that QQ cannot execute agent tools.
It creates only a temporary profile and makes no QQ or model requests.

The patch also contains focused adapter, Markdown presentation, target parsing,
listener recovery and WebSocket-close regression tests. Run them from the
prepared Hermes checkout:

```bash
cd "$HERMES_CHECKOUT"
venv/bin/python -m pytest tests/gateway/test_qqbot.py \
  tests/gateway/test_qq_mobile_format.py \
  tests/tools/test_send_message_target_parse.py -q
```

Use a clean shell without inherited `PYTHONPATH`, QQ credentials or other bot
profiles. These tests do not prove real QQ connectivity, external network-path
reliability, hardware compatibility, or the quality of an operator's document
corpus and configured model. Live acceptance is a separate operator action.
