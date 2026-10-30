"""All-attribute ranges use engine support and never turn projected speeds into observations."""

import pytest

from src.domain.bo3_inference.models import (
    STATS,
    AttributeBoundsRequest,
    AttributeEnvelope,
    CandidateSummary,
    InferenceCheckpoint,
    PointRange,
)
from src.domain.bo3_inference.narration import coaching_context
from src.domain.exceptions import CalcEngineError
from src.services.bo3_inference.attributes import AttributeProjectionService


def checkpoint():
    return InferenceCheckpoint(id="g1:after_game", game=1, phase="after_game", perspective="public",
        outcome="ambiguous", coverage="bounded", summaries=(CandidateSummary(
            pokemon_id="Alice:slot1", species="Gengar", natures=("Modest",), basis="declarations",
            stat_points={stat: PointRange(minimum=0, maximum=32) for stat in STATS}),))


class Attributes:
    def __init__(self, unavailable=False):
        self.requests = []
        self.unavailable = unavailable

    def attributes_batch(self, requests):
        self.requests.extend(requests)
        if self.unavailable:
            raise CalcEngineError("Test unavailable worker")
        return [AttributeEnvelope(species=request.species, supported=True,
            attributes={stat: PointRange(minimum=100, maximum=132) for stat in STATS}) for request in requests]


def test_enrichment_is_cached_and_does_not_modify_existing_ranges_or_future_form_knowledge():
    port = Attributes()
    service = AttributeProjectionService(engine=port)
    original = checkpoint()
    before = service.enrich(original, {})
    after = service.enrich(original, {"Alice:slot1": {"Gengar-Mega"}})
    assert len(port.requests) == 2
    assert {entry.species for entry in before.summaries[0].attributes} == {"Gengar"}
    assert {entry.species for entry in after.summaries[0].attributes} == {"Gengar", "Gengar-Mega"}
    assert not original.summaries[0].attributes
    assert after.summaries[0].stat_points == original.summaries[0].stat_points


def test_unavailable_worker_keeps_inference_and_does_not_invent_stats():
    result = AttributeProjectionService(engine=Attributes(unavailable=True)).enrich(checkpoint(), {})
    assert result.coverage == "bounded"
    assert not result.summaries[0].attributes[0].supported
    assert not result.summaries[0].attributes[0].attributes


def test_battle_speed_projection_is_not_a_numerical_observation_in_coaching():
    original = {"speed": {"faster_speed": 200, "slower_speed": 179, "faster": "Gengar"}, "damage": 50}
    projected = coaching_context(original)
    assert "faster_speed" not in projected["speed"]
    assert "not_observed_or_inferred" in projected["speed"]["basis"]
    assert original["speed"]["faster_speed"] == 200 and projected["damage"] == 50


def test_attribute_request_rejects_incomplete_or_illegal_investment():
    with pytest.raises(ValueError, match="six"):
        AttributeBoundsRequest(species="Gengar", natures=("Modest",), stat_points={"hp": PointRange(minimum=0, maximum=32)})


def test_ui_renders_all_six_attributes_without_changing_point_units():
    from streamlit.testing.v1 import AppTest

    evidence = AttributeProjectionService(engine=Attributes()).enrich(checkpoint(), {})
    script = f'''
from src.domain.bo3_inference.models import Bo3InferenceEvidence, InferenceCheckpoint
from src.ui.series import render_inference
checkpoint = InferenceCheckpoint.model_validate_json({evidence.model_dump_json()!r})
render_inference(Bo3InferenceEvidence(continuity="registered_team", checkpoints=(checkpoint,)))
'''
    app = AppTest.from_string(script, default_timeout=60).run()
    assert not app.exception
    assert len(app.dataframe) == 2
    assert set(STATS).issubset(app.dataframe[1].value.columns)
    assert app.dataframe[1].value.iloc[0]["spe"] == "100-132"



@pytest.mark.parametrize("text", [
    "Mega Gengar achieved a Speed stat of **200**.",
    "Battle mechanics logged an effective Speed of 234.",
    "Vivillon's Speed was 155.",
    "Attack value is 200.",
])
def test_numerical_attribute_claims_cannot_be_narrated_as_observed_exact_stats(text):
    from src.domain.bo3_inference.narration import (
        UNVALIDATED_INVESTMENT_NOTICE,
        contains_numerical_attributes,
    )
    from src.services.ground_truth import explain_champions_only

    assert contains_numerical_attributes(text)
    assert explain_champions_only(lambda correction: text, bo3=True) == UNVALIDATED_INVESTMENT_NOTICE


@pytest.mark.parametrize("text", ["Speed rose by 2 stages.", "Attack was 2 stages higher.", "Shadow Ball dealt 32% damage."])
def test_observed_stages_and_damage_are_not_exact_card_attribute_claims(text):
    from src.domain.bo3_inference.narration import contains_numerical_attributes

    assert not contains_numerical_attributes(text)
