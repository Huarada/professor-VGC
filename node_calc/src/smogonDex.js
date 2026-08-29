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
 * Format resolution: the caller usually passes the REPLAY's format id (e.g. a
 * project-specific/future regulation such as "gen9championsvgc2026regmb").
 * Smogon's live dex only has data for formats it has actually published, so
 * that exact string will almost always come back empty (or, for stats/sets,
 * make @pkmn/smogon throw when the host answers with a 404 HTML page instead
 * of JSON). When that happens, every lookup here falls back to another
 * VGC-tagged format Smogon DOES have for that species (this project is
 * VGC-only) — NEVER to a fully unscoped query. An unscoped fallback used to
 * exist here and was removed after a live bug: for a species Smogon has
 * thin/no VGC coverage for, it silently returned whatever format Smogon's
 * library resolves an unscoped stats/sets call to (in practice, its
 * flagship OU singles metagame) — e.g. a team-improvement question got
 * back a real, official-Smogon-sourced "teammate"/"set" that was entirely a
 * singles pick, indistinguishable from genuine VGC data once it reached the
 * LLM's trusted context (the anti-hallucination prompt rule only checks
 * "does this species literally appear in the JSON", which the wrong-format
 * data trivially satisfied). No VGC-tagged data for a species now means
 * this returns empty/null, not a substitution — the Python composite
 * provider then falls back to the local Chaos file, which IS correctly
 * VGC/regulation-scoped (see ChaosTierIndex). Missing data beats wrong data.
 */

const { Dex } = require('@pkmn/dex');
const { Generations } = require('@pkmn/data');
const { Smogon } = require('@pkmn/smogon');

const gens = new Generations(Dex);
const smogon = new Smogon(fetch); // reuses an in-memory cache across calls

const VGC_HINT = /vgc/i;

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
 * format triggers deep inside @pkmn/smogon's fetch handling. Either way, the
 * caller treats it exactly like "no data for this attempt" and moves on to
 * the next fallback.
 */
async function safeFetch(promiseFactory) {
  try {
    return await promiseFactory();
  } catch (_err) {
    return null;
  }
}

function sortVgcFirst(list, formatOf) {
  return [...list].sort((a, b) => Number(VGC_HINT.test(formatOf(b))) - Number(VGC_HINT.test(formatOf(a))));
}

/** The first VGC-tagged format in a list, or undefined if none is — the
 * exact piece of logic a live bug got wrong (it used to fall back to
 * `formats[0]`, an arbitrary non-VGC format, when no VGC one existed; see
 * this module's header comment). Exported standalone so that regression is
 * unit-testable without mocking @pkmn/smogon's network calls — see
 * test/smogonDex.test.js. */
function firstVgcFormat(formats) {
  return (formats || []).find((f) => VGC_HINT.test(f || ''));
}

/** Every VGC-tagged entry in a list, in VGC-first order — same
 * testability rationale as firstVgcFormat above. */
function vgcOnly(list, formatOf) {
  return sortVgcFirst(list, formatOf).filter((item) => VGC_HINT.test(formatOf(item) || ''));
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

/** Every VGC-tagged format Smogon actually has analyses for, this species'
 * requested format first if it matched. A non-VGC analysis (this species'
 * only Smogon coverage might be e.g. OU singles) is never returned — see
 * this module's header comment for why that's a hard requirement, not a
 * preference. */
async function analyses(genNum, name, format) {
  let raw = format ? await safeFetch(() => smogon.analyses(gen(genNum), name, format)) : null;
  if (!raw || !raw.length) {
    raw = await safeFetch(() => smogon.analyses(gen(genNum), name, undefined));
  }
  if (!raw || !raw.length) return [];
  return vgcOnly(raw.map(mapAnalysis), (a) => a.format);
}

/** A real (published) VGC-tagged format Smogon has for this species, or
 * undefined if it has none — NEVER an arbitrary non-VGC one. */
async function resolveVgcFormat(genNum, name) {
  const list = await safeFetch(() => smogon.analyses(gen(genNum), name, undefined));
  const formats = (list || []).map((a) => a.format).filter(Boolean);
  return firstVgcFormat(formats);
}

async function sets(genNum, name, format) {
  let list = format ? await safeFetch(() => smogon.sets(gen(genNum), name, format)) : null;
  if (!list || !list.length) {
    const resolved = await resolveVgcFormat(genNum, name);
    if (resolved && resolved !== format) {
      list = await safeFetch(() => smogon.sets(gen(genNum), name, resolved));
    }
  }
  // No further, unscoped fallback: see this module's header comment — a
  // species with no VGC-tagged sets returns empty here, not someone else's
  // singles set.
  return list || [];
}

async function stats(genNum, name, format) {
  let s = format ? await safeFetch(() => smogon.stats(gen(genNum), name, format)) : null;
  if (!s) {
    const resolved = await resolveVgcFormat(genNum, name);
    if (resolved && resolved !== format) {
      s = await safeFetch(() => smogon.stats(gen(genNum), name, resolved));
    }
  }
  // No further, unscoped fallback — same reasoning as sets() above.
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

module.exports = {
  speciesInfo, analyses, sets, stats,
  // Exported for direct unit testing (see test/smogonDex.test.js) — pure
  // functions, no network involved, isolating the exact logic a live bug
  // got wrong from @pkmn/smogon's own fetch behavior.
  firstVgcFormat, vgcOnly,
};
