#!/usr/bin/env python3
"""Install a blank profile and plugin; do not enable QQ, login or start services."""
import argparse
import json
import shlex
from pathlib import Path

import yaml
from prepare_hermes import ROOT, verify


def write_new_or_same(path, content):
    if path.exists():
        if path.read_text() != content:
            raise ValueError(f'Refusing to overwrite existing user content: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(content)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-dir', type=Path, required=True)
    parser.add_argument('--hermes-checkout', type=Path, required=True)
    args = parser.parse_args()
    try:
        hermes = args.hermes_checkout.expanduser().resolve()
        if verify(hermes) != 'patched':
            raise ValueError('Apply the Hermes patch before installing the profile.')
        profile = args.profile_dir.expanduser().resolve()
        python = hermes / 'venv/bin/python'
        if not python.is_file():
            raise ValueError('Run prepare_hermes.py --install-deps first.')
        config = yaml.safe_load((ROOT / 'examples/config.yaml').read_text())
        config['mcp_servers']['spacemit_docs'] = {
            'command': str(python), 'args': [str(ROOT / 'cie-qq-ops/rag_server.py')],
            'env': {'HERMES_HOME': str(profile)}, 'enabled': True,
            'connect_timeout': 30, 'timeout': 35,
        }
        # A manual /daily request only previews; it never publishes FAQ or sends independently.
        config['quick_commands']['daily'] = {
            'type': 'exec', 'command': shlex.join([str(python), str(ROOT / 'cie-qq-ops/daily_summary.py'), '--groups-only', '--db', str(profile / 'state.db'), '--faq-candidates-db', str(profile / 'website-answers/candidates.sqlite')])
        }
        plugin = profile / 'plugins/cie-answer-policy'
        files = {
            profile / 'config.yaml': yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            profile / '.env': (ROOT / 'examples/env.example').read_text(),
            plugin / 'location.json': json.dumps({'project_root': str(ROOT)}, indent=2) + '\n',
        }
        for name in ['__init__.py', 'plugin.yaml']:
            files[plugin / name] = (ROOT / 'plugins/cie-answer-policy' / name).read_text()
        # Validate every destination before writing anything, including reruns.
        for path, content in files.items():
            if path.exists() and path.read_text() != content:
                raise ValueError(f'Existing customized file: {path}; no files changed.')
        for path, content in files.items():
            write_new_or_same(path, content)
        print(json.dumps({'profile': str(profile), 'qq_enabled': False, 'gateway_started': False, 'scheduled_jobs_created': 0}))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'Profile installation stopped: {exc}\n')


if __name__ == '__main__':
    main()
