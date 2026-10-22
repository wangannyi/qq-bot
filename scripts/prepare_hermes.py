#!/usr/bin/env python3
"""Prepare or verify the pinned Hermes checkout, without launching its gateway."""
import argparse
import hashlib
import json
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / 'patches/hermes-lock.json').read_text())


def git(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()


def verify(checkout):
    if git(checkout, 'rev-parse', 'HEAD') != LOCK['commit']:
        raise ValueError('Hermes HEAD differs from the tested commit; use a new checkout.')
    names = set(LOCK['files'])
    changed = set(git(checkout, 'diff', '--name-only', 'HEAD').splitlines())
    untracked = set(git(checkout, 'ls-files', '--others', '--exclude-standard').splitlines())
    if (changed | untracked) - names:
        raise ValueError('Unrelated checkout changes detected; use a clean dedicated checkout.')
    states = set()
    for name, hashes in LOCK['files'].items():
        path = checkout / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if digest == hashes['patched']:
            states.add('patched')
        elif digest == hashes['upstream']:
            states.add('upstream')
        else:
            raise ValueError(f'Unexpected local content in {name}; no files changed.')
    if len(states) != 1:
        raise ValueError('Partly applied patch detected; restore a clean dedicated checkout.')
    return next(iter(states))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--install-deps', action='store_true')
    parser.add_argument('--clone-from', default=LOCK['repository'], help='Optional Git mirror, still checked against the pinned commit and file hashes')
    args = parser.parse_args()
    checkout = args.checkout.expanduser().resolve()
    try:
        if not checkout.exists():
            if args.check_only:
                raise ValueError('Checkout does not exist.')
            subprocess.run(['git', 'clone', '--no-checkout', args.clone_from, str(checkout)], check=True)
            subprocess.run(['git', '-C', str(checkout), 'checkout', '--detach', LOCK['commit']], check=True)
        state = verify(checkout)
        patch = ROOT / 'patches' / LOCK['patch']
        subprocess.run(['git', '-C', str(checkout), 'apply', '--check', *(['--reverse'] if state == 'patched' else []), str(patch)], check=True)
        if args.check_only:
            print(json.dumps({'commit': LOCK['commit'], 'patch_state': state, 'compatible': True}))
            return
        if state == 'upstream':
            subprocess.run(['git', '-C', str(checkout), 'apply', str(patch)], check=True)
        assert verify(checkout) == 'patched'
        if args.install_deps:
            if not (3, 11) <= sys.version_info[:2] < (3, 14):
                raise ValueError('Hermes requires Python >=3.11,<3.14.')
            env = checkout / 'venv'
            if not (env / 'pyvenv.cfg').is_file():
                if env.exists() and any(env.iterdir()):
                    raise ValueError('Existing venv directory is not an environment.')
                venv.EnvBuilder(with_pip=True).create(env)
            subprocess.run([str(env / 'bin/python'), '-m', 'pip', 'install', '-e', str(checkout) + '[messaging]', '-r', str(ROOT / 'requirements.txt')], check=True)
        print(json.dumps({'commit': LOCK['commit'], 'patch_state': 'patched', 'gateway_started': False}))
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Preparation stopped: {exc}\n')


if __name__ == '__main__':
    main()
