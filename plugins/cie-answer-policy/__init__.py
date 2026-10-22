"""Hermes plugin installed by scripts/install_profile.py; no embedded machine paths."""
import json
import os
import sys
from pathlib import Path

root = os.environ.get("QQ_BOT_ROOT")
if not root:
    location = Path(__file__).with_name("location.json")
    if not location.is_file():
        raise RuntimeError("Install this plugin with scripts/install_profile.py or set QQ_BOT_ROOT")
    root = json.loads(location.read_text())["project_root"]
ops = Path(root).expanduser().resolve() / "cie-qq-ops"
if not (ops / "answer_policy.py").is_file():
    raise RuntimeError("QQ_BOT_ROOT does not contain cie-qq-ops/answer_policy.py")
sys.path.insert(0, str(ops))
sys.path.insert(1, str(ops.parent / "qq-support-bot"))
from answer_policy import register  # noqa: E402,F401
