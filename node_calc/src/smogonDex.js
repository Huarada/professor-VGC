'use strict';

/**
 * smogonDex.js
 *
 * Thin wrapper around @pkmn/dex + @pkmn/data + @pkmn/smogon. Exposes the
 * official Smogon data the ADR asks for:
 *   - species(): offline Dex info (types, base stats, abilities) — no network.
 *   - analyses(): official natural-language analyses (overview/comments/sets).
 *   - sets(): official competitive sets (moves/item/ability/nature/evs/tera).
 *   - stats(): usage statistics (abilities/items/moves/spreads/teammates/counters).
 *
 * analyses/sets/stats fetch from Smogon's data host at runtime, so they require
 * network access; each is wrapped so a failure returns a structured error and
 * the Python layer can fall back to the local Chaos data.
 *
 * Format resolution: EXACT MATCH ONLY, no fallback to any other format —
 * this used to be more lenient, twice, and both times a live bug proved the
 * leniency actively harmful:
 *
 *   1. (fixed 2026-08-29, first pass) An UNSCOPED fallback query, for a
 *      species Smogon has thin/no VGC coverage for, silently returned
 *      whatever format Smogon's library resolves an unscoped call to (in
 *      practice, its flagship OU singles metagame) — a team-improvement
 *      question got back a real, official-Smogon-sourced "teammate"/"set"
 *      that was entirely a singles pick.
 *   2. (fixed 2026-08-29, second pass) Restricting the fallback to "any
 *      VGC-tagged format" (rather than fully unscoped) was still not
 *      enough: VGC regulations rotate their legal Pokémon pool every few
 *      months, so a real VGC format from a YEAR-OLD regulation
 *      (`gen9vgc2025`, `gen9vgc2023` — confirmed live, both surfaced as
 *      the fallback for a species with no data under the actual battle's
 *      2026 Champions regulation) is just as capable of recommending a
 *      Pokémon that isn't even legal in the CURRENT regulation as an
 *      out-of-format singles pick was. "VGC" is necessary but nowhere
 *      near sufficient for "current metagame".
 *
 * The caller passes the REPLAY's exact format id (e.g. a project-specific/
 * future regulation such as "gen9championsvgc2026regmb"). Smogon's live
 * dex frequently has no data at all for a very new regulation, so this
 * exact string commonly comes back empty (or, for stats/sets, makes
 * @pkmn/smogon throw when the host answers with a 404 HTML page instead of
 * JSON) — that is now a normal, expected outcome, not a failure to work
 * around: it returns empty/null, and the Python composite provider falls
 * back to the local Chaos file, which IS correctly regulation-scoped (see
 * ChaosTierIndex's own, deliberately bounded regulation-fallback, same
 * game family only). Missing data beats wrong data.
 */

const { Dex } = require('@pkmn/dex');
const { Generations } = require('@pkmn/data');
const { Smogon } = require('@pkmn/smogon');

const gens = new Generations(Dex);
const smogon = new Smogon(fetch); // reuses an in-memory cache across calls

function gen(genNum) {
  return gens.get(genNum || 9);
}

function speciesInfo(genNum, name) {
  const sp = gen(genNum).species.get(name);
  if (!sp || !sp.exists) return null;
  return {
    name: sp.name,
    types: sp.types,
    baseStats: sp.baseStats,
    abilities: Object.values(sp.abilities || {}),
    weightkg: sp.weightkg,
    isNonstandard: sp.isNonstandard || null,
  };
}

/**
 * Runs an @pkmn/smogon call, swallowing both rejected promises (network
 * errors) and the "host returned HTML, not JSON" crash that an unknown
 * format triggers deep inside @pkmn/smogon's fetch handling. Either way,
 * the caller treats it exactly like "no data for this format" — there is
 * no further fallback attempt to move on to (see module header).
 */
async function safeFetch(promiseFactory) {
  try {
    return await promiseFactory();
  } catch (_err) {
    return null;
  }
}

function mapAnalysis(a) {
  return {
    format: a.format,
    overview: a.overview || '',
    comments: a.comments || '',
    sets: Object.entries(a.sets || {}).map(([setName, set]) => ({
      name: setName,
      description: set.description || '',
      ability: set.ability,
      item: set.item,
      nature: set.nature,
      moves: set.moves,
      evs: set.evs,
      teratypes: set.teratypes,
    })),
  };
}

/** Analyses for exactly the requested format, or an empty list if Smogon
 * has none — never another format's, VGC-tagged or not (see module
 * header). */
async function analyses(genNum, name, format) {
  if (!format) return [];
  const raw = await safeFetch(() => smogon.analyses(gen(genNum), name, format));
  if (!raw || !raw.length) return [];
  return raw.map(mapAnalysis);
}

/** Competitive sets for exactly the requested format, or an empty list. */
async function sets(genNum, name, format) {
  if (!format) return [];
  const list = await safeFetch(() => smogon.sets(gen(genNum), name, format));
  return list || [];
}

/** Usage statistics for exactly the requested format, or null. */
async function stats(genNum, name, format) {
  if (!format) return null;
  const s = await safeFetch(() => smogon.stats(gen(genNum), name, format));
  if (!s) return null;
  return {
    count: s.count,
    abilities: s.abilities || {},
    items: s.items || {},
    moves: s.moves || {},
    spreads: s.spreads || {},
    stats: s.stats || {},
    teammates: s.teammates || {},
    counters: s.counters || {},
    teraTypes: s.teraTypes || {},
  };
}

module.exports = { speciesInfo, analyses, sets, stats };
