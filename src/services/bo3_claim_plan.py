"""Build and render factual claim plans from validated BO3 checkpoints."""

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any, cast

from src.domain.bo3_inference.citations import bound_evidence, describe_hit
from src.domain.bo3_inference.interpretation import interpret_checkpoint
from src.domain.bo3_inference.models import DamageCitation, InferenceCheckpoint
from src.domain.narrative_claims import NarrativeClaim, NarrativeClaimPlan


def prompt_claim_plan(plan: NarrativeClaimPlan) -> dict[str, Any]:
    """Share checkpoint/identity scope instead of repeating it in every source."""
    result = plan.model_dump(mode="json")
    for claim in result["claims"]:
        source = claim.pop("source")
        claim["source_ref"] = source["basis"] + ":" + source["field"]
    result["source_scope"] = "Each source_ref uses this plan's game/checkpoint_id and its claim's pokemon_id."
    return result


def build_claim_plan(checkpoint: InferenceCheckpoint, question: str,
                     citations: Sequence[DamageCitation] = ()) -> NarrativeClaimPlan:
    """Anchor facts to calculated checkpoint fields, never retrieved-hit counts."""
    claims: list[NarrativeClaim] = []
    required: list[str] = []
    findings = interpret_checkpoint(checkpoint)
    wants_traits = any(word in question.lower() for word in ("spread", "nature", "ivs", "evs", "each pokemon"))
    summaries = {summary.pokemon_id: summary for summary in checkpoint.summaries}
    for mon in findings.pokemon:
        def add(kind: str, value: object, field: str, *, stat: str | None = None, form: str | None = None,
                mandatory: bool = False, basis: str = "checkpoint_declaration",
                baseline: tuple[int, int] | None = None, evidence: tuple[str, ...] = (),
                evidence_label: str = "") -> None:
            payload = {"pokemon_id": mon.pokemon_id, "species": mon.species, "kind": kind, "value": value,
                       "stat": stat, "form": form, "baseline": baseline, "evidence": evidence,
                       "evidence_label": evidence_label,
                       "source": {"checkpoint_id": checkpoint.id, "game": checkpoint.game,
                           "pokemon_id": mon.pokemon_id, "field": field, "basis": basis}}
            identifier = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
            claim = NarrativeClaim.model_validate({"id": identifier, **payload})
            claims.append(claim)
            if mandatory:
                required.append(claim.id)

        if mon.nature_is_declared and len(mon.natures) == 1:
            add("nature", mon.natures[0], "summary.natures", mandatory=wants_traits)
        if mon.declared_item is not None:
            add("item", mon.declared_item, "summary.declared_item", mandatory=wants_traits)
        if mon.declared_base_ability is not None:
            add("base_ability", mon.declared_base_ability, "summary.declared_ability")
        if mon.declared_moves:
            add("moves", mon.declared_moves, "summary.declared_moves", mandatory=wants_traits)
        for form in mon.forms:
            if form.forme_ability and form.form != mon.species:
                add("forme_ability", form.forme_ability, "summary.attributes.forme_ability", form=form.form,
                    mandatory=True, basis="engine_form_ability")
        if checkpoint.outcome != "conflict":
            for stat, bounds in {**mon.declared_native_bounds, **mon.constrained_native_bounds}.items():
                reduction = mon.range_reductions.get(stat)
                summary = summaries.get(mon.pokemon_id)
                label, hits = bound_evidence(checkpoint, summary, stat, citations, limit=3) if summary and reduction and reduction.removed_integer_values else ("", ())
                add("bounds", (bounds.minimum, bounds.maximum), f"summary.stat_points.{stat}", stat=stat,
                    mandatory=True, basis="checkpoint_result",
                    baseline=(reduction.baseline.minimum, reduction.baseline.maximum)
                    if reduction and reduction.removed_integer_values else None,
                    evidence=tuple(describe_hit(hit) for hit in hits), evidence_label=label)
        narrowed = any(change.removed_integer_values for change in mon.range_reductions.values())
        status = ("known_within_supplied_model" if mon.complete_spread_known else
                  "partially_narrowed" if narrowed else "original_spread_unresolved")
        add("spread_status", status, "summary.stat_points_and_checkpoint.coverage", mandatory=True, basis="checkpoint_result")
    limitations = ["IVs are fixed at 31. Bounds describe native Champions stat points, not legacy EVs.",
                   "Independent attribute endpoints do not form one complete legal spread.",
                   f"This checkpoint has {findings.eligible_damage_observations} eligible damage observations; "
                   f"{findings.flagged_damage_observations} observations carry modeling limitations."]
    if checkpoint.coverage != "exhaustive":
        limitations.append("The joint search remains incomplete; compatible bounds do not prove a unique original spread.")
    if checkpoint.outcome == "conflict":
        limitations.append("Evidence conflicts; no new point-bound claim is credited to this checkpoint.")
    return NarrativeClaimPlan(requires_strategy=bool(re.search(r"winning|decision|strategy|turn|adjust|why", question, re.IGNORECASE)),
        game=checkpoint.game, checkpoint_id=checkpoint.id, coverage=checkpoint.coverage,
        claims=tuple(claims), required_claim_ids=tuple(required), limitations=tuple(limitations))


def render_claim_plan(plan: NarrativeClaimPlan, selected: tuple[str, ...], analysis: str = "", *, fallback: bool = False) -> str:
    """Render fact values from the approved catalog, never the model's prose."""
    lines = []
    if fallback:
        lines.append("The generated explanation did not pass evidence validation. The verified findings are shown below.")
    if analysis.strip():
        lines.extend(["**Strategic interpretation**", analysis.strip()])
    lines.append("**Verified teamsheet and spread findings**")
    wanted = set(selected)
    grouped: dict[str, list[str]] = {}
    names = {}
    for claim in plan.claims:
        if claim.id not in wanted:
            continue
        names[claim.pokemon_id] = claim.species
        if claim.kind == "bounds":
            low, high = cast(tuple[int, int], claim.value)
            detail = f"{str(claim.stat).upper()} compatible native points: {low}-{high}"
            if claim.baseline is not None:
                initial_count = claim.baseline[1] - claim.baseline[0] + 1
                remaining = high - low + 1
                reduction = round(100 * (initial_count - remaining) / initial_count, 1)
                detail += f" (initial {claim.baseline[0]}-{claim.baseline[1]}; {reduction}% fewer possibilities)"
            if claim.evidence:
                detail += "; " + claim.evidence_label.lower() + ": " + "; ".join(claim.evidence)
        elif claim.kind == "spread_status":
            detail = {"original_spread_unresolved": "no original stat-point interval narrowed here; the full spread remains unresolved",
                      "partially_narrowed": "some stat-point intervals narrowed; other values and the joint spread remain open",
                      "known_within_supplied_model": "the spread is fixed within the supplied model"}[str(claim.value)]
        elif claim.kind == "forme_ability":
            detail = f"{claim.form} ability: {claim.value}"
        elif claim.kind == "moves":
            detail = "declared/verified moves: " + ", ".join(str(value) for value in claim.value)
        else:
            label = {"nature": "declared nature", "item": "declared/verified item", "base_ability": "base-form ability"}[claim.kind]
            detail = f"{label}: {claim.value or 'none'}"
        grouped.setdefault(claim.pokemon_id, []).append(detail)
    for key, details in grouped.items():
        player = key.rsplit(":slot", 1)[0]
        lines.append(f"- **{names[key]} ({player})**: " + "; ".join(details) + ".")
    lines.extend(plan.limitations)
    return "\n\n".join(lines)
