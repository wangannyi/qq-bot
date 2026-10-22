"""Regression coverage for explicit daily-report recipients.

The daily report normally goes to Hermes' configured QQ home channel.  A
competition operator may instead bind it to one concrete QQ discussion group;
these tests ensure that explicit Hermes targets are forwarded unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import daily_summary


def _capture_subprocess(monkeypatch):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(daily_summary.subprocess, "run", fake_run)
    return calls


def test_deliver_uses_qqbot_home_channel_by_default(monkeypatch, tmp_path):
    """Existing deployments retain the configured home channel unchanged."""

    calls = _capture_subprocess(monkeypatch)
    hermes_bin = tmp_path / "hermes"

    daily_summary.deliver("日报内容", hermes_bin)

    assert calls == [
        (
            [str(hermes_bin), "send", "--to", "qqbot", "--file", "-", "--quiet"],
            {"input": "日报内容", "text": True, "check": True, "timeout": 60},
        )
    ]


def test_deliver_passes_explicit_group_target_to_hermes(monkeypatch, tmp_path):
    """A bound QQ discussion group is not silently replaced with ``qqbot``."""

    calls = _capture_subprocess(monkeypatch)
    hermes_bin = tmp_path / "hermes"
    group_target = "qqbot:A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1"

    daily_summary.deliver("日报内容", hermes_bin, group_target)

    assert calls[0][0] == [
        str(hermes_bin),
        "send",
        "--to",
        group_target,
        "--file",
        "-",
        "--quiet",
    ]
    assert "env" not in calls[0][1]


def test_delivery_target_cli_option_defaults_and_accepts_group_id(monkeypatch):
    """The scheduled service can use the safe default or a configured group."""

    monkeypatch.setattr(sys, "argv", ["daily_summary.py"])
    assert daily_summary.parse_args().delivery_target == "qqbot"

    group_target = "qqbot:A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1"
    monkeypatch.setattr(
        sys,
        "argv",
        ["daily_summary.py", "--delivery-target", group_target],
    )
    assert daily_summary.parse_args().delivery_target == group_target
