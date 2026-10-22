#!/usr/bin/env python3
"""Read-only checks. No credentials are printed and no remote API is called."""
import argparse
import json
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-dir', type=Path)
    parser.add_argument('--hermes-checkout', type=Path)
    args = parser.parse_args()
    checks = {'python_supported': (3, 11) <= sys.version_info[:2] < (3, 14)}
    dependencies = {}
    for name in ['httpx', 'openai', 'PyYAML', 'mcp']:
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = 'missing'
    checks['dependencies_present'] = all(v != 'missing' for v in dependencies.values())
    try:
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE VIRTUAL TABLE smoke USING fts5(body)')
        checks['sqlite_fts5'] = True
    except sqlite3.DatabaseError:
        checks['sqlite_fts5'] = False
    if args.hermes_checkout:
        from prepare_hermes import verify
        try:
            checks['hermes_patched'] = verify(args.hermes_checkout.resolve()) == 'patched'
        except Exception:
            checks['hermes_patched'] = False
    if args.profile_dir:
        import yaml
        try:
            config = yaml.safe_load((args.profile_dir / 'config.yaml').read_text())
            checks['profile_config'] = config.get('cie_support', {}).get('answer_backend') in {'independent', 'website'}
            checks['plugin_installed'] = (args.profile_dir / 'plugins/cie-answer-policy/location.json').is_file()
        except (OSError, ValueError, yaml.YAMLError):
            checks['profile_config'] = False
    print(json.dumps({'checks': checks, 'dependencies': dependencies, 'network_calls': 0, 'qq_sent': False}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
