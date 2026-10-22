"""Portable paths; importing this module never creates state or reads credentials."""
import os
from pathlib import Path


def data_dir():
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return Path(os.environ.get("QQ_BOT_DATA_DIR", str(base / "qq-bot"))).expanduser()


def profile_dir():
    return Path(os.environ.get("HERMES_HOME", str(data_dir() / "profile"))).expanduser()


INDEX = Path(os.environ.get("QQ_BOT_INDEX", str(data_dir() / "docs-v2.sqlite"))).expanduser()
FAQ_REPO = Path(os.environ.get("QQ_BOT_FAQ_REPO", str(data_dir() / "faq"))).expanduser()
FAQ_URL = os.environ.get("QQ_BOT_FAQ_URL", "").strip()
