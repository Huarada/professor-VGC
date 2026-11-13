"""Review-local metagame prefetch while the matchup selector runs."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.domain.models import AnalysisRequest, GameState, MetaContext, SmogonStrategy
from src.domain.regulation import RegulationRoster

if TYPE_CHECKING:
    from src.services.ground_truth import GroundTruthAssembler


@dataclass(frozen=True)
class PrefetchedReviewContext:
    """The same typed context the assembler would load after selection."""

    data_format: str
    species: tuple[str, ...]
    roster: RegulationRoster
    meta_context: MetaContext
    strategies: tuple[SmogonStrategy, ...]


@contextmanager
def prefetch_during_selection(
    assembler: GroundTruthAssembler, request: AnalysisRequest, game_state: GameState,
) -> Iterator[Future[PrefetchedReviewContext] | None]:
    """Overlap only BO3 reviews; preserve the existing BO1 execution path."""
    if request.series_context is None or not assembler.prefetch_enabled:
        yield None
        return
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="bo3-context") as pool:
        yield pool.submit(assembler.prefetch, request, game_state)
