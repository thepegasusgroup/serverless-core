"""Pipeline step-chain executor.

Each pipeline has an ordered `steps` array. Each step is either:
  • model      — call a model with {system, user_template}
  • transform  — deterministic text clean-up

Execution maintains a context {input, prev, step_1, step_2, ...}. Every step
reads the context via {{…}} template substitution; its output is stored as
the next step_N and becomes the new "prev".
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("serverless_core_api.pipeline_exec")


# ---------------------- template substitution ---------------------------------

_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render(template: str, ctx: dict[str, str]) -> str:
    """Replace {{name}} in template with ctx[name]. Unknown keys → empty str."""
    return _TEMPLATE_RE.sub(lambda m: str(ctx.get(m.group(1), "")), template or "")


# ---------------------- transforms -------------------------------------------


def _trim(text: str, _params: dict) -> str:
    return text.strip()


def _collapse_whitespace(text: str, _params: dict) -> str:
    return re.sub(r"\s+", " ", text).strip()


_FENCE_RE = re.compile(
    r"^\s*```[\w-]*\s*\n?(.*?)\n?```\s*$", re.DOTALL,
)


def _strip_markdown_fences(text: str, _params: dict) -> str:
    m = _FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


_ANY_FENCE = re.compile(r"```[\w-]*\s*\n?(.*?)\n?```", re.DOTALL)


def _extract_code_block(text: str, _params: dict) -> str:
    """Return the contents of the FIRST fenced code block in `text`."""
    m = _ANY_FENCE.search(text)
    return m.group(1).strip() if m else text.strip()


def _extract_json(text: str, _params: dict) -> str:
    """Find the first JSON-looking substring and return it (as text).
    Tolerant: scans for {...} or [...] with brace matching.
    """
    text = text.strip()
    # Prefer fenced blocks.
    fenced = _ANY_FENCE.search(text)
    if fenced:
        text = fenced.group(1)
    start = -1
    depth = 0
    opener = ""
    closer = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            if start == -1:
                start = i
                opener = ch
                closer = "}" if ch == "{" else "]"
                depth = 1
            elif ch == opener:
                depth += 1
        elif start != -1 and ch == closer:
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    json.loads(candidate)  # validate
                    return candidate
                except Exception:
                    # keep scanning
                    start = -1
                    depth = 0
    return text  # give up; pass through


def _regex_replace(text: str, params: dict) -> str:
    pattern = params.get("pattern", "")
    replacement = params.get("replacement", "")
    flags = 0
    if params.get("multiline"):
        flags |= re.MULTILINE
    if params.get("ignore_case"):
        flags |= re.IGNORECASE
    if params.get("dotall"):
        flags |= re.DOTALL
    try:
        return re.sub(pattern, replacement, text, flags=flags)
    except re.error as e:
        logger.warning("regex_replace invalid pattern %r: %s", pattern, e)
        return text


def _replace(text: str, params: dict) -> str:
    find = params.get("find", "")
    repl = params.get("replace", "")
    return text.replace(find, repl)


def _strip_prefix(text: str, params: dict) -> str:
    pref = params.get("prefix", "")
    return text[len(pref) :] if pref and text.startswith(pref) else text


def _strip_suffix(text: str, params: dict) -> str:
    suf = params.get("suffix", "")
    return text[: -len(suf)] if suf and text.endswith(suf) else text


TRANSFORMS = {
    "trim": _trim,
    "collapse_whitespace": _collapse_whitespace,
    "strip_markdown_fences": _strip_markdown_fences,
    "extract_code_block": _extract_code_block,
    "extract_json": _extract_json,
    "regex_replace": _regex_replace,
    "replace": _replace,
    "strip_prefix": _strip_prefix,
    "strip_suffix": _strip_suffix,
}


def apply_transform(step: dict[str, Any], text: str) -> str:
    kind = step.get("transform")
    fn = TRANSFORMS.get(kind)
    if not fn:
        raise ValueError(f"Unknown transform '{kind}'")
    return fn(text, step.get("params") or {})


# ---------------------- usage accumulator ------------------------------------


def merge_usage(
    acc: dict[str, int], incoming: dict[str, Any] | None
) -> dict[str, int]:
    if not incoming:
        return acc
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if k in incoming and incoming[k] is not None:
            acc[k] = acc.get(k, 0) + int(incoming[k])
    return acc
