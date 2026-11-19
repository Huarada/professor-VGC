"""Stable, browser-facing views of analysis results and deterministic findings."""

from __future__ import annotations

from typing import Any

from src.domain.bo3_inference.interpretation import interpret_checkpoint
from src.domain.bo3_inference.models import Bo3InferenceEvidence
from src.domain.models import AnalysisResult
from src.domain.replay_view_models import BattleReplay
from src.domain.series import Bo3AnalysisResult, Bo3Evidence


def inference_view(inference: Bo3InferenceEvidence | None) -> dict[str, Any] | None:
    if inference is None:
        return None
    return {
        "iv_policy": inference.iv_policy,
        "units": inference.units,
        "perspective": inference.perspective,
        "continuity": inference.continuity,
        "limitations": list(inference.limitations),
        "checkpoints": [interpret_checkpoint(checkpoint).model_dump(mode="json")
                        for checkpoint in inference.checkpoints if checkpoint.phase == "after_game"],
        "damage_citations": [citation.model_dump(mode="json") for citation in inference.damage_citations],
    }


def preview_view(evidence: Bo3Evidence, inference: Bo3InferenceEvidence | None) -> dict[str, Any]:
    return {"schema_version": 1, "mode": "bo3", "evidence": evidence.model_dump(mode="json"),
            "inference": inference_view(inference)}


def game_view(result: AnalysisResult, replay: BattleReplay, number: int) -> dict[str, Any]:
    return {"number": number, "answer": result.answer, "battle_result": result.battle_result,
            "regulation": result.regulation.model_dump(mode="json") if result.regulation else None,
            "verdicts": [item.model_dump(mode="json") for item in result.verdicts],
            "turn_checks": [item.model_dump(mode="json") for item in result.turn_checks],
            "protect_reads": [item.model_dump(mode="json") for item in result.protect_reads],
            "replay": replay.model_dump(mode="json")}


def bo1_view(result: AnalysisResult, replay: BattleReplay) -> dict[str, Any]:
    return {"schema_version": 1, "mode": "bo1", "answer": result.answer,
            "game": game_view(result, replay, 1)}


def bo3_view(result: Bo3AnalysisResult, replays: list[BattleReplay]) -> dict[str, Any]:
    return {"schema_version": 1, "mode": "bo3", "answer": result.answer,
            "preview": preview_view(result.evidence, result.inference),
            "games": [game_view(game, replay, number) for number, (game, replay)
                      in enumerate(zip(result.games, replays), 1)]}
