"""Regression for deployment continuations and internal rejection diagnostics."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from support_commands import command_lines
from support_reply import InvalidAnswer, validate_answer

INSTALL = "python -m pip install --upgrade 'spacemit-asr>=1.0.4'"
INDEX = "--index-url https://git.spacemit.com/api/v4/projects/33/packages/pypi/simple"
COMMAND = INSTALL + " " + INDEX
SOURCE = {
    "title": "ASR",
    "content": "安装发布包：\n```bash\n" + INSTALL + " \\\n    " + INDEX + "\n```",
    "page_url": "https://www.spacemit.com/asr",
    "source_kind": "official",
    "heading": "安装",
}


def test_continued_install_is_complete_and_source_owned():
    catalog = runtime.evidence_catalog([SOURCE])
    assert [c["text"] for c in catalog[0]["commands"]] == [COMMAND]
    payload = {
        "status": "answered",
        "conclusion": {
            "text": "安装发布包。",
            "evidence": [{"source_id": 0, "evidence_id": 0}],
        },
        "points": [
            {
                "text": "使用官方源安装。",
                "command_id": "0:0",
                "evidence": [{"source_id": 0, "evidence_id": 0}],
            }
        ],
        "follow_up": "",
    }
    resolved = runtime.resolve_catalog(payload, catalog)
    assert validate_answer(resolved, [SOURCE])["points"][0]["command"] == COMMAND
    resolved["points"][0]["command"] = INSTALL
    with pytest.raises(InvalidAnswer, match="command_not_in_source"):
        validate_answer(resolved, [SOURCE])


def test_dangling_continuation_never_becomes_a_command():
    assert command_lines("```bash\n" + INSTALL + " \\") == ["```bash"]
    assert not runtime.evidence_catalog([dict(SOURCE, content=INSTALL + " \\\n```")])[
        0
    ]["commands"]


@pytest.mark.parametrize(
    "message", ["answer_too_long", "unexpected_markup", "secret: provider payload"]
)
def test_internal_rejection_does_not_request_known_environment(monkeypatch, message):
    def rejected(*a, **k):
        raise InvalidAnswer(message)

    monkeypatch.setattr(runtime, "answer_question", rejected)
    result = runtime._run_turn(
        {
            "question": "K3 Bianbu 4.0上如何部署spacemit-asr？",
            "query": "K3 Bianbu 4.0上如何部署spacemit-asr？",
            "sender": "repair-test",
        },
        "repair-test",
    )
    assert result["status"] == "rejected"
    assert "答复暂不可用" in result["text"]
    assert "补充系统版本" not in result["text"]
    assert result["error_code"] == (message if ":" not in message else "invalid_answer")


def test_package_deployment_requires_available_install_not_only_cd():
    answer = {
        "status": "answered",
        "conclusion": {"text": "部署ASR"},
        "points": [{"command": "cd components/model_zoo/asr"}],
    }
    with pytest.raises(
        InvalidAnswer, match="package_deployment_requires_install_command"
    ):
        runtime.validate_package_installation(
            "K3 Bianbu 4.0上部署spacemit-asr", answer, [SOURCE]
        )
    answer["points"] = [{"command": COMMAND}]
    runtime.validate_package_installation(
        "K3 Bianbu 4.0上部署spacemit-asr", answer, [SOURCE]
    )
    answer["points"] = []
    runtime.validate_package_installation(
        "K3如何编译spacemit-asr源码", answer, [SOURCE]
    )


def test_asr_release_view_preserves_literals_and_other_routes():
    from support_sources import asr_package_sources

    source = dict(
        SOURCE,
        path="software/SDK/ros/k3/4.1.1-ASR.md",
        content="ASR组件提供Python绑定。\n\n编译C++示例。\n\n运行 Python 示例前，准备虚拟环境。\n\n方式一，直接安装发布包：\n"
        + SOURCE["content"]
        + "\n\n方式二，安装本地构建wheel。\n\nSenseVoice 默认模型路径为模型目录。",
    )
    selected = asr_package_sources("K3 Bianbu 4.0上如何部署spacemit-asr？", [source])
    assert "编译C++" not in selected[0]["content"]
    assert SOURCE["content"] in selected[0]["content"]
    assert "SenseVoice 默认模型路径" in selected[0]["content"]
    assert "方式二" not in selected[0]["content"]
    for question in [
        "K1 Bianbu上部署spacemit-asr",
        "K3编译spacemit-asr源码",
        "K3部署spacemit-asr流式麦克风示例",
        "K3 spacemit-asr与gateway部署区别",
    ]:
        assert asr_package_sources(question, [source]) == [source]


def test_source_recipe_has_all_actions_without_claiming_version_compatibility():
    import copy

    content = (
        "ASR 组件位于K3 SDK。\n\n运行 Python 示例前，先安装虚拟环境工具。\n```bash\nsudo apt install python3-venv python-is-python3 python3-pip\npython3 -m venv ~/.comm-env\nsource ~/.comm-env/bin/activate\n```\n\n方式一，直接安装发布包：\n"
        + SOURCE["content"]
    )
    source = dict(SOURCE, content=content, route_scope="asr_published_package")
    answer = runtime.package_install_answer("K3 Bianbu 4.0上部署spacemit-asr", [source])
    assert answer["status"] == "partial"
    assert "Bianbu 4.0" in answer["follow_up"]
    assert "补充系统版本" not in answer["follow_up"]
    assert [p["command"] for p in answer["points"]] == [
        "python3 -m venv ~/.comm-env",
        "source ~/.comm-env/bin/activate",
        COMMAND,
    ]
    assert not runtime.package_install_answer("q", [dict(source, route_scope="")])
    broken = copy.deepcopy(source)
    broken["content"] = broken["content"].replace("source ~/.comm-env/bin/activate", "")
    assert runtime.package_install_answer("q", [broken]) is None
