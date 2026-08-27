"""Tests for the shared Firestore write path (chaos_firestore_writer.py),
used by both migrate_chaos_to_firestore.py and
sync_smogon_chaos_to_firestore.py.

No network/credentials needed — strip_empty_keys is pure data
transformation, and write_tier is exercised against the same lightweight
fake Firestore client test_firestore_chaos_repository.py already defines
for the read side.
"""

from __future__ import annotations

from scripts.chaos_firestore_writer import strip_empty_keys, write_tier
from tests.test_firestore_chaos_repository import _FakeFirestoreClient


def test_strip_empty_keys_drops_empty_string_keys_at_every_depth():
    """Smogon's own Chaos export universally includes a `"": <weight>` entry
    under every species' "Moves" map — Firestore's field-path validation
    rejects an empty string as a map key outright, so this is load-bearing,
    not cosmetic (reported: the real migration crashed on this before the
    fix, mid-way through the first tier)."""
    raw = {
        "Moves": {"": 5.2, "Earthquake": 90.1},
        "Checks and Counters": {"": {"p": 0.1}, "Ting-Lu": {"p": 0.6}},
        "nested_list": [{"": 1, "keep": 2}, {"also_keep": 3}],
        "Raw count": 100,
    }
    cleaned = strip_empty_keys(raw)
    assert cleaned == {
        "Moves": {"Earthquake": 90.1},
        "Checks and Counters": {"Ting-Lu": {"p": 0.6}},
        "nested_list": [{"keep": 2}, {"also_keep": 3}],
        "Raw count": 100,
    }


def test_strip_empty_keys_preserves_every_real_key_and_value_unchanged():
    """Every non-empty key, at any depth, must survive byte-for-byte — the
    whole point of this migration is storing the data "as-is", not
    reshaping it."""
    raw = {"Abilities": {"Levitate": 100}, "Items": {"Leftovers": 50}, "Spreads": {"Modest:0/0/0/32/0/32": 40}}
    assert strip_empty_keys(raw) == raw


def test_strip_empty_keys_is_a_noop_on_non_dict_non_list_values():
    assert strip_empty_keys("Garchomp") == "Garchomp"
    assert strip_empty_keys(42) == 42
    assert strip_empty_keys(None) is None


def test_write_tier_upserts_tier_and_species_docs_and_strips_empty_keys():
    client = _FakeFirestoreClient({})
    written = write_tier(
        client, "chaos_tiers", "gen9vgc2025regh-0",
        {"metagame": "gen9vgc2025regh"},
        {"Garchomp": {"Moves": {"": 1, "Earthquake": 90}, "Raw count": 100}},
        progress=False,
    )
    assert written == 1
    tier_doc = client._store[("chaos_tiers", "gen9vgc2025regh-0")]
    assert tier_doc == {"info": {"metagame": "gen9vgc2025regh"}}
    species_doc = client._store[("chaos_tiers", "gen9vgc2025regh-0", "species", "garchomp")]
    assert species_doc == {
        "Moves": {"Earthquake": 90}, "Raw count": 100, "original_name": "Garchomp",
    }


def test_write_tier_batches_beyond_the_single_batch_count_limit():
    """A tier with more species than one batch's worth must still write
    every one of them (the loop's flush-and-continue path), not just the
    first _BATCH_COUNT_LIMIT, and must actually have split into multiple
    real commits to prove the limit was honored, not just eventually
    written some other way."""
    client = _FakeFirestoreClient({})
    species_data = {f"Species{i}": {"Raw count": i} for i in range(120)}  # > _BATCH_COUNT_LIMIT (50)
    written = write_tier(client, "chaos_tiers", "gen9vgc2025regh-0", {}, species_data, progress=False)
    assert written == 120
    species_keys = [k for k in client._store if len(k) == 4 and k[:2] == ("chaos_tiers", "gen9vgc2025regh-0")]
    assert len(species_keys) == 120
    assert client.commit_count == 4  # tier doc (1) + 3 species batches of <=50 (120 = 50+50+20)


def test_write_tier_recovers_by_splitting_when_firestore_rejects_a_commit_as_too_big():
    """Reported live: even a byte-size-aware first-pass batch (well under
    this test's own generous heuristic) still got rejected by REAL
    Firestore with `InvalidArgument("Transaction too big")`, because actual
    wire size includes automatic-index overhead this module can't predict
    from Python (see _BATCH_BYTES_LIMIT's own comment). The fake client's
    `max_commit_bytes` reproduces that same rejection deterministically, so
    this test proves _commit_pairs' adaptive halving is what actually
    recovers — not just that the naive first-pass heuristic happens to
    avoid the problem for these particular fixtures."""
    # max_commit_bytes set well BELOW what write_tier's own first-pass
    # chunking would produce unsplit, forcing at least one real split.
    client = _FakeFirestoreClient({}, max_commit_bytes=50_000)
    species_data = {f"Species{i}": {"blob": "x" * 30_000} for i in range(10)}
    written = write_tier(client, "chaos_tiers", "gen9vgc2025regh-0", {}, species_data, progress=False)
    assert written == 10
    species_keys = [k for k in client._store if len(k) == 4 and k[:2] == ("chaos_tiers", "gen9vgc2025regh-0")]
    assert len(species_keys) == 10


def test_write_tier_flushes_early_on_byte_size_even_under_the_count_limit():
    """Reported: a live sync hit Firestore's real 10 MiB "Transaction too
    big" error on a batch well under 50 documents, because that month's
    per-species entries ran far larger than this test suite's own tiny
    fixtures — the byte-size flush trigger (not just the count one) is what
    actually prevents that, so it needs its own dedicated coverage (via
    commit_count, not just "every doc eventually landed") rather than
    relying on the count-limit test above to exercise it."""
    client = _FakeFirestoreClient({})
    # Each entry is ~200KB — well under _BATCH_COUNT_LIMIT (50) documents,
    # but enough of them must still cross _BATCH_BYTES_LIMIT (3 MB) before
    # all 20 are added, forcing more than one commit.
    big_value = "x" * 200_000
    species_data = {f"Species{i}": {"blob": big_value} for i in range(20)}
    written = write_tier(client, "chaos_tiers", "gen9vgc2025regh-0", {}, species_data, progress=False)
    assert written == 20
    species_keys = [k for k in client._store if len(k) == 4 and k[:2] == ("chaos_tiers", "gen9vgc2025regh-0")]
    assert len(species_keys) == 20
    # 1 (tier doc) + >1 species batches — proves the byte trigger fired
    # despite being nowhere near the 50-document count limit.
    assert client.commit_count > 2
