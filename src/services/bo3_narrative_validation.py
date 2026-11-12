"""Bounded factual validation for the BO3 structured narration contract."""

import json
import re

from pydantic import ValidationError

from src.domain.narrative_claims import NarrativeClaimPlan, NarrativeDraft


def validate_draft(text: str, plan: NarrativeClaimPlan) -> tuple[NarrativeDraft | None, tuple[str, ...]]:
    """Accept catalog IDs only; reject known factual substitutions in free prose.

    This is a constrained factual boundary, not a general semantic proof of every
    strategic interpretation. Unsupported unique-winning-move claims are disallowed.
    """
    candidate = text.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    try:
        draft = NarrativeDraft.model_validate(json.loads(candidate, strict=False))  # raw newlines inside strings are harmless
    except (ValueError, ValidationError):
        return None, ("Return one JSON object with claim_ids and analysis; no factual values or extra fields.",)
    issues = []
    known = {claim.id for claim in plan.claims}
    selected = set(draft.claim_ids)
    if len(selected) != len(draft.claim_ids):
        issues.append("Do not duplicate claim IDs.")
    if selected - known:
        issues.append("Remove unknown or other-checkpoint claim IDs.")
    if set(plan.required_claim_ids) - selected:
        issues.append("Include every required claim ID, including narrowed bounds and unresolved-spread status.")
    if plan.requires_strategy and not draft.analysis.strip():
        issues.append("Answer the strategic question with a conditional interpretation of the supplied battle evidence.")
    prose = draft.analysis.replace("*", "")
    if re.search(r"\b(?:ability|nature|item|common moves|moveset|holds|holding|equipped|EVs|IVs|maxed|maximized)\b", prose, re.IGNORECASE):
        issues.append("Do not restate trait lists in analysis; select their verified claim IDs.")
    if re.search(r"\b(?:Adamant|Bashful|Bold|Brave|Calm|Careful|Docile|Gentle|Hardy|Hasty|Impish|Jolly|Lax|Lonely|Mild|Modest|Naive|Naughty|Quiet|Quirky|Rash|Relaxed|Sassy|Serious|Timid)\b", prose):
        issues.append("Nature values belong to the verified catalog, not free-form analysis.")
    if re.search(r"\b(?:typically|commonly|usually|standard set|common setup|likely maxed)\b", prose, re.IGNORECASE):
        issues.append("Do not substitute common sets for this series' facts.")
    if re.search(r"(?:no|doesn.t (?:contain|provide)|does not (?:contain|provide))\s+(?:specific\s+|any\s+)?"
                 r"(?:information|data|evidence)\s+(?:on|about|for)\s+(?:the\s+)?(?:stats|spreads|IVs|EVs)", prose, re.IGNORECASE):
        issues.append("Distinguish unresolved exact spreads from the supplied computed bounds and declarations.")
    if re.search(r"\b(?:the winning decision|the decisive move|guaranteed victory|guaranteed the win|caused the forfeit)\b",
                 prose, re.IGNORECASE):
        issues.append("Discuss observed turning points as strategic interpretations, not a proven unique cause of victory.")
    # Sets and numerical card stats are rendered from catalog facts. The free
    # prose must not attach a base ability to an explicitly named Mega forme.
    owned = {(claim.pokemon_id, str(claim.value)) for claim in plan.claims if claim.kind in {"base_ability", "forme_ability"}}
    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())
    for sentence in re.split(r"[.!?\n]+", prose):
        normalized = normalize(sentence)
        for claim in plan.claims:
            if claim.kind != "forme_ability" or not claim.form:
                continue
            aliases = {normalize(claim.form), normalize(claim.species) + "mega", "mega" + normalize(claim.species)}
            if any(alias in normalized for alias in aliases):
                # Only this Pokemon's own other ability counts; a sentence may name several Megas.
                if any(normalize(ability) in normalized and normalize(ability) != normalize(str(claim.value))
                       for pokemon_id, ability in owned if pokemon_id == claim.pokemon_id):
                    issues.append("A Mega forme must not be assigned a different/base-form ability.")
    return draft, tuple(dict.fromkeys(issues))
