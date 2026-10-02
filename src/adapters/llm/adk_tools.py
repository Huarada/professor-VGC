"""Google ADK adapter for :class:`~src.adapters.llm.evidence_tools.EvidenceTools`.

ADK auto-wraps a plain, type-hinted callable with a Google-style docstring
into a function tool straight off its signature, so the shared tool core's
bound methods are passed through unchanged — this file only exists to give
the composition root one framework-named entry point.
"""

from __future__ import annotations

from typing import Any

from src.adapters.llm.evidence_tools import EvidenceTools


def build_adk_tools(tools: EvidenceTools) -> list[Any]:
    """Return callables ready to pass straight into ``Agent(tools=[...])``.

    Typed ``list[Any]``: ADK's own ``Agent.tools`` field type
    (``list[Callable[..., Any] | BaseTool | BaseToolset]``) would otherwise
    reject this list under mypy's strict, invariant-``list`` checking, even
    though every element genuinely is such a callable.
    """
    return list(tools.functions())
