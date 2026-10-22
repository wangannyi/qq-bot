from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from qq_support_bot.database import Database
from qq_support_bot.knowledge import _query_terms, _split_markdown, search
from qq_support_bot.service import SupportService, _unique_sources


class KnowledgeTests(TestCase):
    def test_identity_question_uses_quick_answer_without_sources(self):
        service = object.__new__(SupportService)
        answer = service.quick_answer("你是谁？")
        self.assertIsNotNone(answer)
        self.assertIn("进迭时空机器人比赛技术支持助手", answer.text)
        self.assertEqual(answer.sources, [])

    def test_greeting_uses_quick_answer(self):
        service = object.__new__(SupportService)
        answer = service.quick_answer("你好！")
        self.assertIsNotNone(answer)
        self.assertIn("请直接描述问题", answer.text)

    def test_technical_question_does_not_use_quick_answer(self):
        service = object.__new__(SupportService)
        self.assertIsNone(service.quick_answer("K1 如何刷写 Bianbu 系统？"))

    def test_sources_are_limited_to_one_document(self):
        results = [
            self._search_result("文档一", "https://example/1"),
            self._search_result("文档二", "https://example/2"),
        ]
        self.assertEqual([source["title"] for source in _unique_sources(results)], ["文档一"])

    @staticmethod
    def _search_result(title: str, url: str):
        from qq_support_bot.knowledge import SearchResult

        return SearchResult(title, "章节", "path.md", url, "内容", 10)

    def test_split_markdown_tracks_heading(self):
        chunks = _split_markdown("# 标题\n介绍\n## 刷机\n执行命令")
        self.assertEqual(chunks[0], ("标题", "介绍"))
        self.assertEqual(chunks[1], ("刷机", "执行命令"))

    def test_split_markdown_respects_context_budget(self):
        chunks = _split_markdown("# 标题\n" + "内容" * 1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(content) <= 650 for _, content in chunks))

    def test_split_markdown_removes_image_markup(self):
        chunks = _split_markdown("# 刷机\n步骤\n![截图](https://example.invalid/very-long.png)\n完成")
        self.assertEqual(chunks, [("刷机", "步骤\n\n完成")])

    def test_query_terms_include_ascii_and_chinese(self):
        terms = dict(_query_terms("K1 如何安装 Bianbu 系统"))
        self.assertIn("k1", terms)
        self.assertIn("bianbu", terms)
        self.assertIn("安装", terms)

    def test_query_terms_expand_flashing_synonyms(self):
        terms = dict(_query_terms("K1 如何刷写系统"))
        self.assertIn("刷写", terms)
        self.assertIn("烧录", terms)
        self.assertIn("刷机", terms)
        self.assertIn("titan", terms)

    def test_query_terms_expand_llm_and_deployment_synonyms(self):
        terms = dict(_query_terms("K1 如何部署 LLM"))
        self.assertIn("llm", terms)
        self.assertIn("大语言模型", terms)
        self.assertIn("大模型", terms)
        self.assertIn("部署", terms)
        self.assertIn("安装", terms)

    def test_k1_llm_deployment_prefers_competition_guide(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            with database.connect() as connection:
                documents = (
                    (
                        "1",
                        "competition/竞赛教程/k1_llm.md",
                        "基于 K1 AI CPU 大模型部署落地",
                        "本赛题要求基于进迭开源的 Llama.cpp 进行部署。",
                    ),
                    (
                        "2",
                        "software/SDK/ros/k1/speech_llm.md",
                        "语音输入大模型输出",
                        "支持一键部署语音输入和大模型输出演示。",
                    ),
                    (
                        "3",
                        "courses/ai/llm.md",
                        "大语言模型简介",
                        "介绍大语言模型的发展和应用。",
                    ),
                )
                for doc_id, path, title, content in documents:
                    connection.execute(
                        """
                        INSERT INTO documents
                        (id, path, title, page_url, source_url, content_hash, synced_at)
                        VALUES (?, ?, ?, ?, ?, 'hash', 'now')
                        """,
                        (doc_id, path, title, f"https://example/{doc_id}", f"https://source/{doc_id}"),
                    )
                    connection.execute(
                        "INSERT INTO chunks (document_id, ordinal, heading, content) VALUES (?, 0, ?, ?)",
                        (doc_id, title, content),
                    )
            results = search(database, "k1如何部署llm")
            self.assertTrue(results)
            self.assertEqual(results[0].path, "competition/竞赛教程/k1_llm.md")

    def test_search_prefers_title_match(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            with database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO documents
                    (id, path, title, page_url, source_url, content_hash, synced_at)
                    VALUES ('1', 'software/bianbu/flash.md', 'Bianbu 刷机指南', 'https://example/1',
                            'https://source/1', 'hash', 'now')
                    """
                )
                connection.execute(
                    "INSERT INTO chunks (document_id, ordinal, heading, content) VALUES ('1', 0, '刷机', '使用刷机工具安装系统')"
                )
            results = search(database, "Bianbu 如何刷机")
            self.assertTrue(results)
            self.assertEqual(results[0].title, "Bianbu 刷机指南")

    def test_flash_search_reserves_official_tool_manual(self):
        with TemporaryDirectory() as directory:
            database = Database(Path(directory) / "test.db")
            with database.connect() as connection:
                for doc_id, path, title in (
                    ("1", "hardware/k1.md", "K1 刷机"),
                    ("2", "software/bianbu.md", "Bianbu 烧录"),
                    ("3", "other/flash.md", "镜像烧写"),
                    ("4", "tools/user_guide/flasher_user_guide.md", "刷机工具使用手册"),
                ):
                    connection.execute(
                        """
                        INSERT INTO documents
                        (id, path, title, page_url, source_url, content_hash, synced_at)
                        VALUES (?, ?, ?, ?, ?, 'hash', 'now')
                        """,
                        (doc_id, path, title, f"https://example/{doc_id}", f"https://source/{doc_id}"),
                    )
                    heading = "单机烧录" if doc_id == "4" else "刷机"
                    connection.execute(
                        "INSERT INTO chunks (document_id, ordinal, heading, content) VALUES (?, 0, ?, ?)",
                        (doc_id, heading, title),
                    )
            results = search(database, "K1 如何刷写 Bianbu", limit=3)
            self.assertIn("tools/user_guide/flasher_user_guide.md", {item.path for item in results})
