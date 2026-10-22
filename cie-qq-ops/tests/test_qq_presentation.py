import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from qq_presentation import render_reply


def test_renders_known_sections_with_qq_custom_markdown_only():
    rendered = render_reply(
        "方法一：Ollama\n"
        "适用于 Bianbu。\n"
        "1、安装工具。\n\n"
        "赛事选择（依据组织方赛题说明）：\n"
        "使用 llama.cpp。\n\n"
        "待补充：系统版本。\n\n"
        "参考文档：\n"
        "[🔗Ollama 部署指南](https://example.com/doc)"
    )

    assert "## 方法一：**Ollama**" in rendered
    assert "1. 安装工具。" in rendered
    assert "## 🎯 赛事选择\n**依据组织方赛题说明**" in rendered
    assert "⚠️ **待补充**：系统版本。" in rendered
    assert "## 📚 参考文档" in rendered
    assert "[🔗Ollama 部署指南](https://example.com/doc)" in rendered
    assert "```" not in rendered and "|" not in rendered


def test_formats_human_review_notice_as_a_compact_quote():
    assert render_reply("需要人工确认：请提供完整报错。") == (
        "> ⚠️ **需要人工确认**：请提供完整报错。"
    )


def test_is_idempotent_for_already_rendered_reply():
    rendered = "## 方法一：**Ollama**\n\n1. 安装工具。\n\n## 📚 参考文档"
    assert render_reply(rendered) == rendered
