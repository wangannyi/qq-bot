"""Exercise source selection against competing concept and application pages."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from retrieval import create_index  # noqa: E402
from support_sources import search_sources  # noqa: E402


@pytest.fixture
def index(tmp_path):
    fixtures = [
        (
            "concept",
            "ABI介绍",
            "# ABI介绍\nABI规范函数调用，是硬件平台、操作系统和编译器之间的约定。",
        ),
        (
            "tools",
            "RISC-V工具链",
            "# readelf\nreadelf用于查看ELF文件头信息。\n\n# mabi\n-mabi指定ABI约定，例如lp64d表示long和指针为64位。",
        ),
        (
            "secureboot",
            "内核ABI安全启动",
            "# 内核ABI安全启动签名\n内核ABI安全启动生成linux-image-<ABI>签名包。",
        ),
        (
            "network",
            "联网",
            "# 联网\n本节以Bianbu GNOME桌面系统为例。\n\n## 查看IP地址\n使用ifconfig查看inet字段确认IPv4地址。\n```bash\nifconfig\n```",
        ),
        (
            "rootfs",
            "Bianbu启动",
            "# Bianbu启动\nBianbu启动后应该先收集系统信息。\n\n## 环境\n宿主机安装qemu。",
        ),
        ("ros", "ROS2联网", "# ROS2联网排查\nBianbu ROS2无法联网时排查ROS2通信配置。"),
        ("python", "Python使用指南", "# Python使用指南\nBianbu Python运行指南。"),
        ("asr", "spacemit-asr", "# spacemit-asr部署\nK3部署spacemit-asr。"),
    ]
    docs = [
        dict(
            id=name,
            path=f"software/{name}.md",
            title=title,
            body=body,
            source_kind="official",
            page_url=f"https://www.spacemit.com/{name}",
            source_url="",
        )
        for name, title, body in fixtures
    ]
    path = tmp_path / "docs.sqlite"
    create_index(path, docs, datetime.now(timezone.utc).isoformat())
    return path


@pytest.mark.parametrize(
    "query",
    [
        "RISC-V 64位程序与开发板系统的ABI如何确认兼容？",
        "RISC-V 64位程序与开发板系统的ABI如何检查兼容？",
    ],
)
def test_abi_inspection_retrieves_tools_not_only_definition(index, query):
    found = search_sources(query, index=index)["sources"]
    tool = next(s for s in found if s["path"] == "software/tools.md")
    assert "readelf" in tool["content"] and "-mabi" in tool["content"]


@pytest.mark.parametrize(
    "query",
    [
        "Bianbu启动后无法联网，应该先收集哪些接口和路由信息？",
        "Bianbu启动后无法联网，应该先收集哪些接口和路由信息？\n用户明确提供的环境：Bianbu",
    ],
)
def test_network_inspection_retrieves_ip_check_and_keeps_scope(index, query):
    found = search_sources(query, index=index)["sources"]
    assert found[0]["path"] == "software/network.md"
    assert "GNOME" in found[0]["content"] and "ifconfig" in found[0]["content"]
    assert all(s["path"] != "software/rootfs.md" for s in found)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("ABI介绍是什么", "concept"),
        ("K3安全启动内核ABI如何确认", "secureboot"),
        ("Bianbu ROS2无法联网怎么排查", "ros"),
        ("Bianbu启动后无法运行Python", "python"),
        ("K3部署spacemit-asr", "asr"),
        ("Bianbu启动如何配置", "rootfs"),
    ],
)
def test_existing_definition_application_and_startup_routes(index, query, expected):
    assert (
        search_sources(query, index=index)["sources"][0]["path"]
        == f"software/{expected}.md"
    )
