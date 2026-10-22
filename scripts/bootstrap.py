#!/usr/bin/env python3
"""Create a local dependency environment; never enable QQ or schedule anything."""
import argparse
import subprocess
import sys
import venv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv', type=Path, default=Path('.venv'))
    parser.add_argument('--dev', action='store_true')
    args = parser.parse_args()
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        parser.error('Use Python 3.11, 3.12 or 3.13 for the published Hermes integration.')
    root = Path(__file__).resolve().parents[1]
    if not (args.venv / 'pyvenv.cfg').exists():
        if args.venv.exists() and any(args.venv.iterdir()):
            parser.error('Refusing a nonempty directory which is not a virtual environment.')
        venv.EnvBuilder(with_pip=True).create(args.venv)
    python = args.venv.resolve() / 'bin/python'
    requirements = root / ('requirements-dev.txt' if args.dev else 'requirements.txt')
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
    print('Dependencies ready. QQ, document sync, daily reports and FAQ publishing remain disabled.')


if __name__ == '__main__':
    main()
