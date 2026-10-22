"""Source-owned shell lines, with explicit shell continuations preserved."""

from __future__ import annotations


def command_lines(content: str) -> list[str]:
    """Join backslash-newline only; never emit fragments of a continued line."""
    result, pending, shell_block = [], "", False
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            shell_block = not shell_block and line[3:].lower() in {
                "",
                "sh",
                "bash",
                "shell",
                "console",
            }
            pending = ""
            result.append(line)
            continue
        if pending and (not line or line.startswith("```")):
            pending = ""
            continue
        joined = pending + line
        if shell_block and joined.endswith("\\"):
            pending = joined[:-1].rstrip() + " "
            continue
        result.append(joined)
        pending = ""
    return result
