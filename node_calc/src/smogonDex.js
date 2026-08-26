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
 * of JSON). Rather than surface that as "no data", every lookup here falls
 * back to an UNSCOPED query (no format filter) so genuine official Smogon
 * data for a real, published format of the same generation is still used —
 * preferring VGC-tagged formats, since this project is VGC-only.
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

/** Every format Smogon actually has analyses for, VGC ones first. */
async function analyses(genNum, name, format) {
  let raw = format ? await safeFetch(() => smogon.analyses(gen(genNum), name, format)) : null;
  if (!raw || !raw.length) {
    raw = await safeFetch(() => smogon.analyses(gen(genNum), name, undefined));
  }
  if (!raw || !raw.length) return [];
  return sortVgcFirst(raw.map(mapAnalysis), (a) => a.format);
}

/** The best real (published) VGC-hinted format Smogon has for this species. */
async function resolveVgcFormat(genNum, name) {
  const list = await safeFetch(() => smogon.analyses(gen(genNum), name, undefined));
  const formats = (list || []).map((a) => a.format).filter(Boolean);
  if (!formats.length) return undefined;
  return formats.find((f) => VGC_HINT.test(f)) || formats[0];
}

async function sets(genNum, name, format) {
  let list = format ? await safeFetch(() => smogon.sets(gen(genNum), name, format)) : null;
  if (!list || !list.length) {
    const resolved = await resolveVgcFormat(genNum, name);
    if (resolved && resolved !== format) {
      list = await safeFetch(() => smogon.sets(gen(genNum), name, resolved));
    }
  }
  if (!list || !list.length) {
    list = await safeFetch(() => smogon.sets(gen(genNum), name, undefined));
  }
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
  if (!s) {
    s = await safeFetch(() => smogon.stats(gen(genNum), name, undefined));
  }
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
