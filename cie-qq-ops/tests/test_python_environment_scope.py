"""Environment setup must cite general manuals, not incidental camera steps."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from retrieval import create_index, retrieve
from support_reply import InvalidAnswer
from support_sources import generic_python_environment, search_sources


@pytest.fixture
def index(tmp_path):
    path = tmp_path / "docs.sqlite"
    docs = []
    sources = [
        (
            "python",
            "Python 使用指南",
            "# Python 使用指南\nBianbu 系统的 Python 开发。\n\n## 创建虚拟环境\n"
            "创建 Python 虚拟环境并激活。\n\n```\npython3 -m venv myenv\nsource myenv/bin/activate\n```\n\n"
            "## 使用 uv\n### 创建虚拟环境\nK3 的 uv 示例。\n\n```\nuv venv myvenv --python 3.14\n```\n\n"
            "## JupyterLab\n### 创建虚拟环境\nJupyter 的 Python 环境。\n\n```\npip install jupyterlab\n```",
        ),
        (
            "camera",
            "3.1.3 Python 使用 USB 相机",
            "# Python 使用 USB 相机\n\n## 创建虚拟环境\n"
            + "K1 Python 创建虚拟环境 USB 摄像头 camera。" * 80
            + "\n\n```\npython3 -m venv camera_env\nsource camera_env/bin/activate\npip install opencv-python\n```",
        ),
    ]
    for name, title, body in sources:
        docs.append(
            dict(
                id=name,
                path=f"software/{name}.md",
                title=title,
                body=body,
                source_kind="official",
                page_url=f"https://www.spacemit.com/docs/{name}",
                source_url="",
            )
        )
    create_index(path, docs, datetime.now(timezone.utc).isoformat())
    return path


@pytest.fixture(autouse=True)
def reset_context():
    runtime._contexts.clear()
    yield
    runtime._contexts.clear()


@pytest.mark.parametrize(
    "question",
    [
        "K1板子如何创建python虚拟环境",
        "K3如何创建虚拟环境",
        "python -m venv 怎么使用",
        "Python virtual environment setup",
        "怎样激活虚拟环境",
        "如何退出虚拟环境",
    ],
)
def test_general_environment_returns_scoped_manual_not_application(index, question):
    sources = search_sources(question, index=index)["sources"]
    assert sources and all(s["title"] == "Python 使用指南" for s in sources)
    text = "\n".join(s["content"] for s in sources)
    assert "python3 -m venv myenv" in text and "source myenv/bin/activate" in text
    assert not any(
        word in text
        for word in ["camera_env", "opencv-python", "uv venv", "jupyterlab", "3.14"]
    )


def test_switching_from_camera_does_not_change_environment_query_or_sources(index):
    question = "K1板子如何创建python虚拟环境"
    cold, _ = runtime.prepare(question, "fresh", "alice")
    runtime.prepare("K1怎么使用usb摄像头", "existing", "alice")
    resumed, _ = runtime.prepare(question, "existing", "alice")
    assert cold == resumed and "摄像头" not in resumed
    for query in [cold, resumed]:
        assert [s["title"] for s in search_sources(query, index=index)["sources"]] == [
            "Python 使用指南"
        ]


def test_explicit_camera_environment_keeps_camera_document(index):
    sources = search_sources("K1 Python USB相机如何创建虚拟环境", index=index)[
        "sources"
    ]
    assert any("USB 相机" in s["title"] for s in sources)


def test_explicit_uv_request_keeps_uv_instructions(index):
    sources = search_sources("K3使用uv创建虚拟环境", index=index)["sources"]
    assert any("uv venv myvenv --python 3.14" in s["content"] for s in sources)


def test_incidental_camera_content_cannot_be_only_evidence(tmp_path):
    path = tmp_path / "camera-only.sqlite"
    create_index(
        path,
        [
            dict(
                id="cam",
                path="camera.md",
                title="Python USB相机使用",
                source_kind="official",
                page_url="https://www.spacemit.com/camera",
                source_url="",
                body="# Python USB相机使用\n## 创建虚拟环境\npython3 -m venv camera_env",
            )
        ],
        datetime.now(timezone.utc).isoformat(),
    )
    assert search_sources("K1怎么创建Python虚拟环境", index=path)["sources"] == []


def test_legacy_retrieval_defaults_still_return_camera_document(index):
    result = retrieve("K1 Python USB相机虚拟环境", path=index)
    assert any("USB 相机" in item["title"] for item in result["results"])


def test_unrelated_application_not_named_in_exclusion_list_is_not_a_python_manual(
    tmp_path,
):
    path = tmp_path / "app.sqlite"
    create_index(
        path,
        [
            dict(
                id="app",
                path="app.md",
                title="ExampleAgent",
                source_kind="official",
                page_url="https://www.spacemit.com/app",
                source_url="",
                body="# ExampleAgent\n## 创建虚拟环境\npython3 -m venv app_env\nsource app_env/bin/activate",
            )
        ],
        datetime.now(timezone.utc).isoformat(),
    )
    assert not search_sources("如何使用venv创建虚拟环境", index=path)["sources"]


def test_separate_activation_heading_is_preserved_with_creation(tmp_path):
    path = tmp_path / "steps.sqlite"
    create_index(
        path,
        [
            dict(
                id="python",
                path="python.md",
                title="Python 使用指南",
                source_kind="official",
                page_url="https://www.spacemit.com/python",
                source_url="",
                body="# Python 使用指南\n## 步骤1：安装虚拟环境工具\nsudo apt install python3-venv\n"
                "## 步骤2：创建虚拟环境\npython3 -m venv myenv\n"
                "## 步骤3：进入环境\nsource myenv/bin/activate\n"
                "## JupyterLab\npip install jupyterlab",
            )
        ],
        datetime.now(timezone.utc).isoformat(),
    )
    sources = search_sources("如何使用venv创建虚拟环境", index=path)["sources"]
    assert len(sources) == 1
    assert "python3 -m venv myenv" in sources[0]["content"]
    assert "source myenv/bin/activate" in sources[0]["content"]
    assert "jupyterlab" not in sources[0]["content"]


@pytest.mark.parametrize(
    "commands",
    [
        ["sudo apt install python3-venv"],
        ["python3 -m venv myenv"],
        ["python3 -m venv myenv", "source different/bin/activate"],
    ],
)
def test_setup_answer_cannot_omit_create_activate_pair_or_mix_directories(commands):
    source = {"content": "python3 -m venv myenv\nsource myenv/bin/activate"}
    answer = {
        "status": "answered",
        "points": [{"command": command} for command in commands],
    }
    with pytest.raises(InvalidAnswer, match="matching_create_and_activate"):
        runtime.validate_environment_steps("K1如何创建Python虚拟环境", answer, [source])


def test_complete_setup_or_activation_only_question_passes():
    source = {"content": "python3 -m venv myenv\nsource myenv/bin/activate"}
    answer = {
        "status": "answered",
        "points": [{"command": line} for line in source["content"].splitlines()],
    }
    runtime.validate_environment_steps("K1如何创建Python虚拟环境", answer, [source])
    runtime.validate_environment_steps(
        "如何激活虚拟环境", {"status": "answered", "points": []}, [source]
    )
    runtime.validate_environment_steps(
        "创建虚拟环境的原理是什么", {"status": "answered", "points": []}, [source]
    )


def test_incomplete_sources_do_not_require_inventing_activation_command():
    runtime.validate_environment_steps(
        "如何创建Python虚拟环境",
        {"status": "answered", "points": []},
        [{"content": "python3 -m venv myenv"}],
    )


def test_incomplete_generated_setup_gets_bounded_repair(monkeypatch):
    create = "python3 -m venv myenv"
    activate = "source myenv/bin/activate"
    source = dict(
        title="Python 使用指南",
        content="创建并激活虚拟环境。\n" + create + "\n" + activate,
        page_url="https://www.spacemit.com/python",
        path="python.md",
        heading="创建虚拟环境",
        source_kind="official",
    )

    def payload(commands):
        return {
            "status": "answered",
            "conclusion": {
                "text": "创建并激活虚拟环境。",
                "evidence": [{"source_id": 0, "quote": "创建并激活虚拟环境。"}],
            },
            "points": [
                {
                    "text": "执行命令。",
                    "command": command,
                    "evidence": [{"source_id": 0, "quote": command}],
                }
                for command in commands
            ],
            "follow_up": "",
        }

    responses = iter(
        [payload([create]), payload([create]), payload([create, activate])]
    )
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        return next(responses), {}

    monkeypatch.setattr(runtime, "generate", generate)
    monkeypatch.setattr(
        runtime, "search_sources", lambda q, **kwargs: {"query": q, "sources": [source]}
    )
    result = runtime.answer_question(
        "K1怎么创建Python虚拟环境",
        config={"cie_support": {"supplemental_search": False}},
    )
    assert len(calls) == 3
    assert "matching_create_and_activate" in calls[2]["feedback"]["error"]
    assert create in result["text"] and activate in result["text"]


@pytest.mark.parametrize(
    "question",
    [
        "LLM虚拟环境如何创建",
        "在虚拟环境中安装ROS2",
        "Python USB摄像头创建虚拟环境",
        "JupyterLab虚拟环境安装",
        "K1介绍",
        "Python有哪些版本",
        "摄像头识别不到",
    ],
)
def test_other_tasks_are_not_routed_to_general_environment(question):
    assert not generic_python_environment(question)
