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
from src.domain.models import AnalysisRequest  # noqa: E402
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

    def validate_request(self, request: AnalysisRequest, regulation: str | None = None) -> None:
        Container(self.settings).validate_request(request, regulation)

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


def test_bo3_ui_reviews_ordered_games_and_keeps_bo1_result_separate(monkeypatch):
    from src.domain.series import Bo3AnalysisRequest
    from src.services.series_analysis import Bo3AnalysisService
    from tests.test_bo3_analysis import game
    from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
    from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
    from tests.test_bo3_inference import AnalyticProjection

    class SeriesContainer(_FakeContainer):
        def validate_series(self, request: Bo3AnalysisRequest, regulation=None):
            return Container(self.settings).validate_series(request, regulation)

        def build_bo3_pipeline(self, provider=None, orchestrator=None, regulation=None):
            return Bo3AnalysisService(
                parser=ShowdownReplayParser(), pipeline_factory=self.build_pipeline,
                llm=FakeLLM("{}", "Alice won the series 2-0."), prompts=PROMPTS,
                inference=Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticProjection()),
            )

    monkeypatch.setattr("src.ui.battle_panel.http_head_ok", lambda url, timeout=2.0: False)
    at = AppTest.from_file(_APP, default_timeout=120)
    container = SeriesContainer()
    at.session_state["container"] = container
    at.run()
    at.text_area[0].input(_LOG_ATTACKED)
    at.button[0].click().run()
    assert not at.exception
    original = at.session_state["last_result"]
    at.radio[0].set_value("Best of three").run()
    assert [at.text_area(key=f"bo3_source_{i}").label for i in range(1, 4)] == [f"Game {i} replay JSON, log or URL" for i in range(1, 4)]
    assert any(area.label == "Teamsheet JSON (optional)" for area in at.text_area)
    at.text_area(key="bo3_source_1").input(game())
    at.text_area(key="bo3_source_2").input(game(move="Protect"))
    next(button for button in at.button if button.label == "Analyze BO3").click().run()
    assert not at.exception
    assert at.session_state["bo3_result"].evidence.score == {"Alice": 2, "Bob": 0}
    assert at.session_state["bo3_result"].inference is not None
    assert any(heading.value == "Stat-point inference (BO3)" for heading in at.subheader)
    assert at.session_state["last_result"] == original
    assert any(slider.key.startswith("bo3_game_") for slider in at.slider)
    at.radio[0].set_value("Best of one").run()
    assert not at.exception
    assert at.session_state["last_result"] == original


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


@pytest.mark.parametrize("missing", [(), (1,)])
def test_bo3_import_fills_official_slots_without_building_a_paid_pipeline(monkeypatch, missing):
    from src.services.series_import import SeriesReplayImporter
    from tests.test_series_import import Gateway, ROOT, URLS

    class ImportContainer(_FakeContainer):
        def import_bo3_replays(self, text):
            return SeriesReplayImporter(gateway=Gateway(missing=missing), parser=ShowdownReplayParser()).import_series(text)

    monkeypatch.setattr("src.ui.battle_panel.http_head_ok", lambda url, timeout=2.0: False)
    at = AppTest.from_file(_APP, default_timeout=120)
    container = ImportContainer()
    at.session_state["container"] = container
    at.run()
    at.radio[0].set_value("Best of three").run()
    at.text_area(key="bo3_import_source").input(ROOT if missing else URLS[2] + "\n" + URLS[1])
    next(button for button in at.button if button.label == "Import BO3").click().run()
    assert not at.exception
    assert container.pipelines_built == 0
    assert at.session_state["bo3_import_receipt"].missing_games == missing
    for number in range(1, 4):
        payload = at.text_area(key=f"bo3_source_{number}").value
        if number in missing:
            assert payload == ""
        else:
            assert f"Game {number}" in json.loads(payload)["log"]
    assert "bo3_result" not in at.session_state


def _analyze_as_openai(at: AppTest) -> None:
    at.sidebar.selectbox[0].select("openai")
    at.run()

    at.text_area[0].input(_LOG_ATTACKED)
    at.text_input[0].input("Should Talonflame have protected on turn 3?")
    next(b for b in at.button if b.label == "Analyze").click()
    at.run()


def test_import_retrieves_and_prefills_public_natures_without_a_paid_analysis(monkeypatch):
    from src.adapters.teamsheet.showdown_teamsheet import ShowdownTeamSheetParser
    from src.domain.series_import import ImportedReplay, ReplayLocation, SeriesImportResult
    from tests.test_public_teamsheets import public_games

    class PublicContainer(_FakeContainer):
        def import_bo3_replays(self, text):
            games = public_games()
            return SeriesImportResult(games=tuple(ImportedReplay(number=number, payload=json.dumps(game),
                location=ReplayLocation(kind="replay", id=game["id"],
                                        url="https://replay.pokemonshowdown.com/" + game["id"]))
                for number, game in enumerate(games, 1)), teamsheet=ShowdownTeamSheetParser().parse(games))

    monkeypatch.setattr("src.ui.battle_panel.http_head_ok", lambda url, timeout=2.0: False)
    at = AppTest.from_file(_APP, default_timeout=120)
    container = PublicContainer()
    at.session_state["container"] = container
    at.run()
    at.radio[0].set_value("Best of three").run()
    at.text_area(key="bo3_import_source").input("https://play.pokemonshowdown.com/game-bestof3-gen9championsvgc2026regmcbo3-2702587956")
    next(button for button in at.button if button.label == "Import BO3").click().run()
    assert not at.exception
    value = json.loads(at.text_area(key="bo3_teamsheet").value)
    assert value["registered_team"]
    assert len(value["pokemon"]) == 36
    assert next(entry for entry in value["pokemon"] if entry["species"] == "Delphox")["nature"] == "Timid"
    assert any("12 declared natures" in caption.value for caption in at.caption)
    assert container.pipelines_built == 0


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
    assert any("Daily limit reached" in e.value for e in at.error)
    assert container.pipelines_built == 2  # the refused third click never built a pipeline


def _analyze_replay_as_openai(at: AppTest, replay_text: str, regulation: str = "auto") -> None:
    at.sidebar.selectbox[0].select("openai")
    next(s for s in at.sidebar.selectbox if s.label == "Regulation").select(regulation)
    at.run()
    at.text_area[0].input(replay_text)
    next(b for b in at.button if b.label == "Analyze").click()
    at.run()


def test_refused_replay_does_not_spend_quota(monkeypatch):
    """Reported: the quota was consumed before the replay was read, so a
    malformed paste (or a replay of another regulation) cost a daily analysis."""
    monkeypatch.setattr("src.ui.battle_panel.http_head_ok", lambda url, timeout=2.0: False)
    container = _FakeContainer(
        UsageQuotaService(InMemoryQuotaStore(), {"openai": 2}, secret="test")
    )
    at = AppTest.from_file(_APP, default_timeout=120)
    at.session_state["container"] = container
    at.run()

    _analyze_replay_as_openai(at, "|switch|p1a: Flutter Mane|")
    assert not at.exception
    assert any("could not be read" in e.value for e in at.error)

    reg_mc_replay = "|tier|[Gen 9 Champions] VGC 2026 Reg M-C\n" + _LOG_ATTACKED
    _analyze_replay_as_openai(at, reg_mc_replay, "mb")
    assert not at.exception
    assert any("pinned to" in e.value for e in at.error)

    assert container.pipelines_built == 0
    assert any("openai: 2 of 2 analyses left today" in c.value for c in at.sidebar.caption)
