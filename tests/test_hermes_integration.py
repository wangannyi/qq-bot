"""Opt-in actual Hermes dispatcher contract, never QQ or a model API."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = os.environ.get('HERMES_CHECKOUT')
pytestmark = [pytest.mark.hermes, pytest.mark.skipif(not CHECKOUT, reason='Set HERMES_CHECKOUT to the patched pinned checkout')]


def test_actual_dispatch_intercepts_qq_and_denies_agent_tools(tmp_path):
    code = r'''
from pathlib import Path
import importlib.util, json, os, sys
root, checkout, home = map(Path, sys.argv[1:])
sys.path[:0] = [str(checkout), str(root / 'cie-qq-ops')]
os.environ['HERMES_HOME'] = str(home)
os.environ['QQ_BOT_ROOT'] = str(root)
import yaml
home.mkdir()
config = yaml.safe_load((root / 'examples/config.yaml').read_text())
(home / 'config.yaml').write_text(yaml.safe_dump(config))
import hermes_cli.plugins as plugins
from hermes_cli.middleware import run_llm_execution_middleware, run_tool_execution_middleware
import support_runtime
# Discover the installed plugin through Hermes, not a direct call to its register function.
import shutil
plugin_dir = home / 'plugins/cie-answer-policy'
shutil.copytree(root / 'plugins/cie-answer-policy', plugin_dir, ignore=shutil.ignore_patterns('__pycache__'))
(plugin_dir / 'location.json').write_text(json.dumps({'project_root':str(root)}))
plugins.discover_plugins(force=True)
manager = plugins.get_plugin_manager()
assert manager._middleware.get('llm_execution'), 'Plugin failed to load'
support_runtime._run_turn = lambda *a, **k: {'text': 'Synthetic supported reply.', 'usage': {}}
plugins.invoke_hook('pre_llm_call', session_id='synthetic-turn', user_message='K1 synthetic question', platform='qqbot', sender_id='synthetic-user')
def forbidden(*a, **k):
    raise AssertionError('generic provider or agent tool executed')
reply = run_llm_execution_middleware({'model':'synthetic'}, forbidden, session_id='synthetic-turn', platform='qqbot')
assert reply.choices[0].message.content == 'Synthetic supported reply.'
assert not reply.choices[0].message.tool_calls
support_runtime.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('synthetic failure'))
reply = run_llm_execution_middleware({'model':'synthetic'}, forbidden, session_id='synthetic-turn', platform='qqbot')
assert reply.choices[0].message.content == support_runtime.UNAVAILABLE_REPLY
blocked = run_tool_execution_middleware('terminal', {'command':'synthetic'}, forbidden, platform='qqbot')
assert 'does not execute' in blocked
assert not (home / 'state.db').exists()
assert not (home / 'website-answers').exists()
print(json.dumps({'actual_dispatch':True,'network_calls':0,'qq_sent':False,'agent_tools_executed':0}))
'''
    env = dict(os.environ, HERMES_HOME=str(tmp_path / 'unused'), PYTHONNOUSERSITE='1')
    result = subprocess.run([sys.executable, '-c', code, str(ROOT), CHECKOUT, str(tmp_path / 'profile')],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"actual_dispatch": true' in result.stdout
