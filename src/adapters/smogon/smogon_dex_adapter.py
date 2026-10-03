"""Official Smogon data via @pkmn/smogon (Node IPC): ``get_strategy``
(analyses), ``get_stats`` (usage) and ``get_sets`` (sets). Needs network;
failures raise ``StrategyKnowledgeError`` so the composite falls back to Chaos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.adapters.node_ipc import NodeIpcClient
from src.adapters.smogon.archetype_signals import infer_archetypes
from src.domain.exceptions import CalcEngineError, StrategyKnowledgeError
from src.domain.models import PokemonMetaSummary, SmogonStrategy

_STAT_LABELS = ("HP", "Atk", "Def", "SpA", "SpD", "Spe")


def describe_smogon_set(s: dict[str, Any]) -> str:
    """One-line rendering of a raw Smogon set (also used by the semantic retriever)."""

    def first(x: Any) -> str:
        if isinstance(x, list):
            return x[0] if x else ""
        return x or ""

    moves = ", ".join(
        (m[0] if isinstance(m, list) else m) for m in (s.get("moves") or []) if m
    )
    return f"{s.get('name', 'Set')}: {first(s.get('ability'))} @ {first(s.get('item'))} — {moves}"


class SmogonDexAdapter:
    """StrategyKnowledgeProvider backed by official @pkmn/smogon data."""

    def __init__(
        self,
        server_script: str | Path | None = None,
        node_binary: str = "node",
        gen: int = 9,
        top_n: int = 4,
        timeout_seconds: float = 30.0,
        ipc: NodeIpcClient | None = None,
    ) -> None:
        self._gen = int(gen)
        self._top_n = max(1, int(top_n))
        if ipc is not None:
            self._ipc = ipc
        elif server_script is not None:
            self._ipc = NodeIpcClient(server_script, node_binary, timeout_seconds)
        else:  # pragma: no cover - misconfiguration guard
            raise ValueError("SmogonDexAdapter requires server_script or ipc")

    def _call(self, cmd: str, name: str, metagame: str | None) -> Any:
        try:
            response = self._ipc.request(
                {"cmd": cmd, "gen": self._gen, "name": name, "format": metagame}
            )
        except CalcEngineError as exc:
            raise StrategyKnowledgeError(f"Smogon dex IPC failed: {exc}") from exc
        if response.get("ok") is False:
            raise StrategyKnowledgeError(
                f"Smogon dex '{cmd}' unavailable for {name}: {response.get('error')}"
            )
        return response.get("result")

    # -- StrategyKnowledgeProvider -------------------------------------- #

    def get_strategy(
        self, species: str, *, metagame: str | None = None, question: str | None = None
    ) -> SmogonStrategy:
        """Plain strategy from the first (VGC-preferred) analysis; ``question`` is
        unused here (the semantic retriever wraps this to use it).
        """
        analyses = self._call("analyses", species, metagame)
        if not analyses:
            raise StrategyKnowledgeError(f"No Smogon analyses for {species}")
        analysis = analyses[0]  # newest/most relevant format first
        overview = " ".join(
            part for part in (analysis.get("overview"), analysis.get("comments")) if part
        ).strip()
        sets = analysis.get("sets", [])
        common_sets = [describe_smogon_set(s) for s in sets[: self._top_n]]
        moves: list[str] = []
        abilities: list[str] = []
        for s in sets:
            for mv in s.get("moves", []) or []:
                name = mv if isinstance(mv, str) else (mv[0] if mv else "")
                if name and name not in moves:
                    moves.append(name)
            ability_field = s.get("ability")
            ability_name = (
                ability_field[0] if isinstance(ability_field, list) and ability_field
                else ability_field if isinstance(ability_field, str)
                else ""
            )
            if ability_name and ability_name not in abilities:
                abilities.append(ability_name)
        return SmogonStrategy(
            species=species,
            overview=f"[Smogon official: {analysis.get('format', 'gen')}] {overview}"
            if overview
            else f"[Smogon official] sets: {', '.join(s.get('name', '') for s in sets)}",
            common_sets=common_sets,
            common_teammates=[],
            # Abilities too: trapping cores often rest on Shadow Tag / Arena Trap.
            archetypes=infer_archetypes(moves, abilities),
        )

    def get_analyses_raw(
        self, species: str, *, metagame: str | None = None
    ) -> list[dict[str, Any]]:
        """Every Smogon analysis for the species, all formats (the semantic
        retriever's raw material).
        """
        analyses = self._call("analyses", species, metagame)
        return analyses or []

    # -- usage statistics (team-synergy suggestions) -------------------- #

    def get_stats(
        self, species: str, *, metagame: str | None = None
    ) -> PokemonMetaSummary:
        stats = self._call("stats", species, metagame)
        if not stats:
            raise StrategyKnowledgeError(f"No Smogon stats for {species}")
        return PokemonMetaSummary(
            top_abilities=self._top(stats.get("abilities", {})),
            top_items=self._top(stats.get("items", {})),
            top_moves=self._top(stats.get("moves", {}), self._top_n + 2),
            top_spreads=self._top_spreads(stats.get("spreads", {})),
            threats_winrate=self._top_counters(stats.get("counters", {})),
            source=f"smogon-official@{metagame or f'gen{self._gen}'}",
        )

    def get_teammates(
        self, species: str, *, metagame: str | None = None
    ) -> dict[str, float]:
        stats = self._call("stats", species, metagame)
        if not stats:
            return {}
        return self._top(stats.get("teammates", {}), self._top_n)

    # -- competitive sets (moveset/item/ability/EV advice) -------------- #

    def get_sets(self, species: str, *, metagame: str | None = None) -> list[dict[str, Any]]:
        sets = self._call("sets", species, metagame)
        return sets or []

    # -- helpers --------------------------------------------------------- #

    def _top(self, mapping: dict[str, Any], count: int | None = None) -> dict[str, float]:
        n = count or self._top_n
        ordered = sorted(mapping.items(), key=lambda kv: kv[1], reverse=True)[:n]
        total = sum(v for _, v in mapping.items()) or 1.0
        return {k: round(float(v) / total, 3) for k, v in ordered if k}

    def _top_spreads(self, spreads: dict[str, Any]) -> list[str]:
        ordered = sorted(spreads.items(), key=lambda kv: kv[1], reverse=True)[: self._top_n]
        return [self._format_spread(s) for s, _ in ordered]

    @staticmethod
    def _format_spread(spread: str) -> str:
        # @pkmn spreads look like "Jolly:0/252/0/0/4/252"
        try:
            nature, evs = spread.split(":")
            values = [int(x) for x in evs.split("/")]
            body = " / ".join(f"{v} {lbl}" for v, lbl in zip(values, _STAT_LABELS))
            return f"{nature} ({body})"
        except (ValueError, AttributeError):
            return spread

    def _top_counters(self, counters: dict[str, Any]) -> dict[str, float]:
        # counters values are [n, koWeight, switchWeight]; use the first as weight.
        ordered = sorted(
            counters.items(),
            key=lambda kv: (kv[1][0] if isinstance(kv[1], (list, tuple)) else kv[1]),
            reverse=True,
        )[: self._top_n]
        return {
            k: round(float(v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else 0.0), 2)
            for k, v in ordered
        }

    def close(self) -> None:
        self._ipc.close()
