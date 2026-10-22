"""Public installer safety and portability, with no production credentials."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blank_config_disables_network_side_effects():
    config = yaml.safe_load((ROOT / 'examples/config.yaml').read_text())
    assert config['platforms']['qqbot']['enabled'] is False
    assert config['platforms']['qqbot']['extra']['group_allow_from'] == []
    assert config['platforms']['qqbot']['extra']['dm_policy'] == 'disabled'
    assert config['cie_support']['supplemental_search'] is False
    assert config['quick_commands'] == {} and config['mcp_servers'] == {}
    assert config['platform_toolsets']['qqbot'] == []
    assert config['custom_providers'][0]['key_env'] == 'QQ_BOT_MODEL_API_KEY'


def test_install_profile_is_idempotent_and_never_starts_processes(tmp_path, monkeypatch):
    installer = script('install_profile')
    checkout = tmp_path / 'hermes'
    (checkout / 'venv/bin').mkdir(parents=True)
    (checkout / 'venv/bin/python').write_text('synthetic executable placeholder')
    profile = tmp_path / 'profile'
    monkeypatch.setattr(installer, 'verify', lambda p: 'patched')
    monkeypatch.setattr(sys, 'argv', ['install_profile', '--profile-dir', str(profile), '--hermes-checkout', str(checkout)])
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('installer launched a process'))
    installer.main()
    first = {str(p.relative_to(profile)): p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    installer.main()
    assert first == {str(p.relative_to(profile)): p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    assert not (profile / 'state.db').exists() and not (profile / 'auth.json').exists()
    config = yaml.safe_load((profile / 'config.yaml').read_text())
    assert config['platforms']['qqbot']['enabled'] is False
    command = config['quick_commands']['daily']['command']
    assert '--send' not in command and '--faq-publish' not in command and '--faq-stage' not in command
    (profile / 'config.yaml').write_text('customized: true\n')
    with pytest.raises(SystemExit):
        installer.main()
    assert (profile / 'config.yaml').read_text() == 'customized: true\n'


def test_offline_demo_works_without_private_profile(tmp_path):
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp_path),
           'HERMES_HOME': str(tmp_path / 'absent'), 'QQ_BOT_DATA_DIR': str(tmp_path / 'data'),
           'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/offline_demo.py')],
                            env=env, text=True, capture_output=True, check=True)
    receipt = json.loads(result.stdout.splitlines()[-1])
    assert receipt['demo'] == 'passed' and receipt['model_calls'] == 0
    assert not receipt['qq_sent'] and not receipt['persistent_database_created']
    assert not (tmp_path / 'absent').exists() and not (tmp_path / 'data').exists()


def test_missing_organizer_inputs_do_not_scan_private_paths(tmp_path, monkeypatch):
    import support_paths
    import sync_docs
    monkeypatch.delenv('QQ_BOT_RULES_DOCX', raising=False)
    monkeypatch.setattr(support_paths, 'FAQ_REPO', tmp_path / 'absent')
    assert sync_docs.local_documents() == []


def test_profile_env_credentials_do_not_read_other_auth(tmp_path, monkeypatch):
    from support_credentials import model_credentials
    config = {'model': {'provider': 'custom:fixture'}, 'cie_support': {'model_api_key_env': 'SYNTHETIC_MODEL_KEY'}}
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(config))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('SYNTHETIC_MODEL_KEY', 'test-only-key')
    assert model_credentials() == (config, 'test-only-key')
    monkeypatch.delenv('SYNTHETIC_MODEL_KEY')
    with pytest.raises(ValueError):
        model_credentials()


def test_report_preview_never_stages_publishes_or_refreshes(monkeypatch, tmp_path):
    import daily_summary
    calls = Mock(side_effect=AssertionError('preview attempted a write'))
    monkeypatch.setattr(daily_summary, 'sync_faq', calls)
    args = SimpleNamespace(faq_path=tmp_path / 'faq.md', faq_repo=tmp_path,
                           faq_candidates_db=tmp_path / 'candidates.sqlite')
    assert daily_summary._faq_sync_for_run(args, []) is None
    calls.assert_not_called()


def test_faq_stage_cannot_modify_a_live_checkout(monkeypatch, tmp_path):
    import daily_summary
    repo = tmp_path / 'faq'
    (repo / '.git').mkdir(parents=True)
    faq = repo / 'faq.md'
    faq.write_text('Existing reviewed FAQ\n')
    calls = Mock(side_effect=AssertionError('live FAQ modified'))
    monkeypatch.setattr(daily_summary, 'sync_faq', calls)
    args = SimpleNamespace(faq_path=faq, faq_repo=repo, faq_stage=True,
                           faq_candidates_db=tmp_path / 'candidates.sqlite')
    result = daily_summary._faq_sync_for_run(args, [])
    assert result['added'] == 0 and not result['failed']
    assert '显式' in result['detail'] and '20:00' not in result['detail']
    assert faq.read_text() == 'Existing reviewed FAQ\n'
    assert not args.faq_candidates_db.exists()
    calls.assert_not_called()


def test_event_policy_is_explicit_and_contains_no_bundled_organizer_prose(monkeypatch, tmp_path):
    import support_extracts
    monkeypatch.delenv('QQ_BOT_RULES_POLICY', raising=False)
    assert support_extracts.event_policy() == {}
    path = tmp_path / 'policy.json'
    policy = {k: 'Synthetic exact clause for ' + k for k in ['models', 'inference', 'deployment']}
    path.write_text(json.dumps(policy))
    monkeypatch.setenv('QQ_BOT_RULES_POLICY', str(path))
    assert support_extracts.event_policy() == policy


@pytest.mark.parametrize('backend', ['independent', None])
def test_qq_execution_failure_is_closed_not_generic_agent(monkeypatch, backend):
    import qq_guard
    monkeypatch.setattr(qq_guard.support_runtime, 'settings', lambda: {'cie_support': {'answer_backend': backend}})
    monkeypatch.setattr(qq_guard.support_runtime, 'execute', Mock(side_effect=RuntimeError('synthetic provider failure')))
    terminal = Mock(side_effect=AssertionError('generic agent called'))
    response = qq_guard.execute({'model': 'fixture'}, terminal, platform='qqbot', session_id='synthetic')
    assert not response.choices[0].message.tool_calls
    assert response.choices[0].message.content == qq_guard.support_runtime.UNAVAILABLE_REPLY
    terminal.assert_not_called()


def test_qq_tool_execution_cannot_run_hardware_or_shell():
    import qq_guard
    terminal = Mock(side_effect=AssertionError('tool executed'))
    assert 'does not execute' in qq_guard.tool_execute('terminal', {'command': 'synthetic'}, terminal, platform='qqbot')
    terminal.assert_not_called()
    other = Mock(return_value='other platform unchanged')
    assert qq_guard.tool_execute('terminal', {}, other, platform='other') == 'other platform unchanged'


def test_independent_runtime_fallback_stays_closed(monkeypatch):
    import qq_guard
    monkeypatch.setattr(qq_guard.support_runtime, 'settings', lambda: {'cie_support': {'answer_backend': 'independent'}})
    monkeypatch.setattr(qq_guard.support_runtime, 'execute', lambda request, next_call, **kwargs: next_call(request))
    terminal = Mock(side_effect=AssertionError('generic provider called'))
    response = qq_guard.execute({'model': 'fixture'}, terminal, platform='qqbot')
    assert response.choices[0].message.content == qq_guard.support_runtime.UNAVAILABLE_REPLY
    terminal.assert_not_called()


def test_other_platform_and_explicit_website_preserve_execution(monkeypatch):
    import qq_guard
    callback = Mock(return_value='legacy')
    assert qq_guard.execute({}, callback, platform='other') == 'legacy'
    monkeypatch.setattr(qq_guard.support_runtime, 'settings', lambda: {'cie_support': {'answer_backend': 'website'}})
    assert qq_guard.execute({}, callback, platform='qqbot') == 'legacy'
