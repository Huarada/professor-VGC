"""Smoke test of the real Streamlit app (``streamlit.testing.v1.AppTest``).

The UI used to be one ~1700-line module; it is now split into theme/landing/
loading/battle_panel/results/audio/icons modules. This drives the actual
``src/ui/app.py`` script: the idle page, then a full Analyze click with an
injected container (native pipeline over fakes — no network, no keys; the
sprite reachability HEAD checks are stubbed out).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")

from streamlit.testing.v1 import AppTest  # noqa: E402

from src.adapters.memory.conversation_memory import InMemoryConversationMemory  # noqa: E402
from src.adapters.parsers.showdown_parser import ShowdownReplayParser  # noqa: E402
from src.config import Settings  # noqa: E402
from src.domain.interfaces import AnalysisPipeline  # noqa: E402
from src.domain.replay_view_models import BattleReplay  # noqa: E402
from src.services.analysis_service import AnalysisService  # noqa: E402
from src.services.container import Container  # noqa: E402
from src.services.selection_service import LLMSelectionService  # noqa: E402
from src.services.usage_quota import UsageQuotaService  # noqa: E402
from tests.conftest import PROMPTS, SAMPLE_DIR, FakeLLM, build_evidence  # noqa: E402
from tests.test_decision_review import _LOG_ATTACKED, _ScriptedCalc  # noqa: E402
from tests.test_usage_quota import InMemoryQuotaStore  # noqa: E402

_APP = str(Path(__file__).resolve().parent.parent / "src" / "ui" / "app.py")


class _FakeContainer:
    settings = Settings(_env_file=None)

    def __init__(self, quota: UsageQuotaService | None = None) -> None:
        self.quota = quota or UsageQuotaService(None, {}, secret="")
        self.pipelines_built = 0

    def usage_quota(self) -> UsageQuotaService:
        return self.quota

    def regulation_choices(self) -> dict[str, str]:
        return {"auto": "Auto (the replay's regulation)", "mb": "Reg M-B only"}

    def data_warnings(self) -> list[str]:
        return []

    def resolve_replay_text(self, text: str) -> str:
        return text

    def parse_replay_for_viewer(self, text: str) -> BattleReplay:
        return Container.parse_replay_for_viewer(text)

    def build_pipeline(
        self, provider: str | None = None, orchestrator: str | None = None,
        regulation: str | None = None,
    ) -> AnalysisPipeline:
        self.pipelines_built += 1
        llm = FakeLLM(
            json.dumps({"focus_species": ["Garchomp", "Talonflame"],
                        "matchups": [["Garchomp", "Talonflame"]], "rationale": "ui"}),
            "Talonflame should have protected on turn 3.",
        )
        return AnalysisService(
            parser=ShowdownReplayParser(), selector=LLMSelectionService(llm, prompts=PROMPTS),
            evidence=build_evidence(SAMPLE_DIR / "gen9championsvgc2026regmb.json", _ScriptedCalc()),
            llm=llm, memory=InMemoryConversationMemory(), prompts=PROMPTS,
        )


def test_idle_page_renders():
    at = AppTest.from_file(_APP, default_timeout=60)
    at.run()
    assert not at.exception
    assert [b.label for b in at.button] == ["Analyze", "Reset conversation"]


def test_analysis_flow_renders_answer_and_decision_review(monkeypatch):
    monkeypatch.setattr("src.ui.battle_panel.http_head_ok", lambda url, timeout=2.0: False)
    at = AppTest.from_file(_APP, default_timeout=120)
    at.session_state["container"] = _FakeContainer()
    at.run()
    at.text_area[0].input(_LOG_ATTACKED)
    at.text_input[0].input("Should Talonflame have protected on turn 3?")
    next(b for b in at.button if b.label == "Analyze").click()
    at.run()

    assert not at.exception
    assert not at.error
    assert any("Talonflame should have protected" in m.value for m in at.markdown)
    captions = "\n".join(c.value for c in at.caption)
    assert "KO threat: Garchomp's Rock Slide" in captions
    assert "Switching Talonflame out to Amoonguss" in captions
    assert "Tailwind (confirmed in Talonflame's moveset" in captions


def _analyze_as_openai(at: AppTest) -> None:
    at.sidebar.selectbox[0].select("openai")
    at.run()
    at.text_area[0].input(_LOG_ATTACKED)
    at.text_input[0].input("Should Talonflame have protected on turn 3?")
    next(b for b in at.button if b.label == "Analyze").click()
    at.run()


def test_openai_quota_counts_down_then_blocks_without_calling_the_model(monkeypatch):
    monkeypatch.setattr("src.ui.battle_panel.http_head_ok", lambda url, timeout=2.0: False)
    container = _FakeContainer(
        UsageQuotaService(InMemoryQuotaStore(), {"openai": 2}, secret="test")
    )
    at = AppTest.from_file(_APP, default_timeout=120)
    at.session_state["container"] = container
    at.run()

    _analyze_as_openai(at)
    assert not at.error
    assert any("openai: 1 of 2 analyses left today" in c.value for c in at.sidebar.caption)

    _analyze_as_openai(at)
    assert not at.error
    assert any("openai: 0 of 2 analyses left today" in c.value for c in at.sidebar.caption)

    _analyze_as_openai(at)
    assert not at.exception
    assert any("UsageLimitExceededError" in e.value for e in at.error)
    assert container.pipelines_built == 2  # the refused third click never built a pipeline
