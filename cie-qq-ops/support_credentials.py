"""Read only the current profile's configured model credentials."""

import json
import os
from pathlib import Path

import yaml


def model_credentials():
    profile = Path(os.environ["HERMES_HOME"])
    config = yaml.safe_load((profile / "config.yaml").read_text())
    env_name = config.get("cie_support", {}).get("model_api_key_env", "")
    if env_name:
        key = os.environ.get(env_name, "").strip()
        if not key:
            raise ValueError("Configured model API key environment variable is empty")
        return config, key
    auth = json.loads((profile / "auth.json").read_text())
    pool = auth.get("credential_pool", {}).get(config["model"]["provider"], [])
    key = next(item["access_token"] for item in pool if item.get("access_token"))
    return config, key
