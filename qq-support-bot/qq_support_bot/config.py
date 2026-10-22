from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


@dataclass(frozen=True)
class Settings:
    root: Path
    qq_app_id: str
    qq_app_secret: str
    llm_base_url: str
    llm_model: str
    llm_timeout_seconds: int
    db_path: Path
    doc_language: str
    doc_prefixes: tuple[str, ...]
    retrieval_limit: int
    max_answer_chars: int
    log_level: str
    daily_report_group_openid: str
    daily_report_hour: int
    daily_report_minute: int
    doc_sync_interval_hours: int

    @classmethod
    def load(cls) -> "Settings":
        root = _project_root()
        _load_dotenv(root / ".env")
        default_data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "qq-bot"
        data_home = Path(os.environ.get("QQ_BOT_DATA_DIR", str(default_data)))
        db_path = Path(os.getenv("KB_DB_PATH", str(data_home / "standalone/support.db")))
        if not db_path.is_absolute():
            db_path = root / db_path
        prefixes = tuple(
            item.strip().strip("/")
            for item in os.getenv("DOC_PREFIXES", "").split(",")
            if item.strip()
        )
        report_time = os.getenv("DAILY_REPORT_TIME", "21:00").strip()
        try:
            report_hour, report_minute = (int(part) for part in report_time.split(":", 1))
            if not (0 <= report_hour <= 23 and 0 <= report_minute <= 59):
                raise ValueError
        except ValueError as error:
            raise ValueError("DAILY_REPORT_TIME 必须使用 HH:MM 格式") from error
        return cls(
            root=root,
            qq_app_id=os.getenv("QQ_APP_ID", "").strip(),
            qq_app_secret=os.getenv("QQ_APP_SECRET", "").strip(),
            llm_base_url=os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/"),
            llm_model=os.getenv("LLM_MODEL", "Qwen2.5-1.5B-Instruct-Q4_0.gguf"),
            llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "180")),
            db_path=db_path,
            doc_language=os.getenv("DOC_LANGUAGE", "zh"),
            doc_prefixes=prefixes,
            retrieval_limit=int(os.getenv("RETRIEVAL_LIMIT", "3")),
            max_answer_chars=int(os.getenv("MAX_ANSWER_CHARS", "1700")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            daily_report_group_openid=os.getenv("DAILY_REPORT_GROUP_OPENID", "").strip(),
            daily_report_hour=report_hour,
            daily_report_minute=report_minute,
            doc_sync_interval_hours=int(os.getenv("DOC_SYNC_INTERVAL_HOURS", "24")),
        )
