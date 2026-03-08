from __future__ import annotations

import re
from pathlib import Path

from pr_agent_context.constants import DEFAULT_PROMPT_TEMPLATE, SUPPORTED_TEMPLATE_PLACEHOLDERS
from pr_agent_context.domain.models import TemplateDiagnostics

PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*}}")
LEFTOVER_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*}}")


def load_prompt_template(path: Path | None) -> tuple[str, str | None, str]:
    if path is None:
        return DEFAULT_PROMPT_TEMPLATE.strip(), None, "built_in"
    return path.read_text(encoding="utf-8").strip(), str(path), "file"


def render_prompt_template(
    *,
    template_text: str,
    template_source: str,
    template_path: str | None,
    values: dict[str, str],
) -> tuple[str, TemplateDiagnostics]:
    placeholders = PLACEHOLDER_RE.findall(template_text)
    unsupported = sorted(set(placeholders) - set(SUPPORTED_TEMPLATE_PLACEHOLDERS))
    if unsupported:
        raise ValueError("Unsupported prompt template placeholder(s): " + ", ".join(unsupported))

    template_shell = PLACEHOLDER_RE.sub("", template_text)
    if "{{" in template_shell or "}}" in template_shell:
        raise ValueError("Malformed prompt template: unmatched '{{' or '}}'.")

    rendered = PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], template_text)
    if LEFTOVER_PLACEHOLDER_RE.search(rendered):
        raise ValueError("Malformed prompt template: unresolved placeholder remained after render.")

    prompt_preamble_inserted = False
    if values["prompt_preamble"] and "prompt_preamble" not in placeholders:
        rendered = f"{values['prompt_preamble']}\n\n{rendered}"
        prompt_preamble_inserted = True

    diagnostics = TemplateDiagnostics(
        template_source="built_in" if template_source == "built_in" else "file",
        template_path=template_path,
        placeholders_used=sorted(set(placeholders)),
        prompt_preamble_inserted=prompt_preamble_inserted,
    )
    return _normalize_template_output(rendered), diagnostics


def _normalize_template_output(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    normalized_lines: list[str] = []
    blank_streak = 0
    for line in lines:
        if line.strip():
            blank_streak = 0
            normalized_lines.append(line)
            continue
        if blank_streak >= 1:
            continue
        normalized_lines.append("")
        blank_streak += 1
    return "\n".join(normalized_lines).strip()
