'use strict';
/**
 * Generates full-information Champions BO3 series with the official Showdown simulator.
 *
 *   node generate.js --count 30 --seed 1 --out ../../.cache/sim-corpus [--format gen9championsvgc2026regmbbo3]
 *
 * For every series it writes bo3-public-<id>.json (spectator logs, what a saved replay contains) and
 * bo3-truth-<id>.json (the random native stat points and natures of BOTH teams). Spreads are legal
 * (0-32 per stat, 66 total) and random, so only the evidence in the logs can identify them.
 *
 * With --teams DIR the teams are not random: every bo3-public-showteam-*.json in DIR (real public series)
 * lends both of its open team sheets (species, item, ability, moves, nature) and the generator only draws
 * the hidden spreads, --variants N times per series (default 1), --spreads random|meta|mixed (default mixed).
 * Teams the validator rejects are skipped and reported. --teams defaults to --format
 * gen9championsvgc2026regmcbo3 (registered below); without --teams the default stays Reg M-B.
 */
const fs = require('fs');
const path = require('path');
// Reg M-C is not in the package (0.11.11 ships M-A and M-B). Same mod and rules as M-B with the Past/Future legality
// filter lifted, so the real M-C rosters (Rillaboom, Indeedee, new Megas...) validate; mechanics are the mod's own.
require('pokemon-showdown/dist/config/formats').Formats.push({name: '[Gen 9 Champions] VGC 2026 Reg M-C (Bo3)',
	mod: 'champions', gameType: 'doubles', ruleset: ['Flat Rules', '!Obtainable', 'VGC Timer', 'Force Open Team Sheets', 'Best of = 3']});
const {Dex, Teams, TeamValidator, BattleStream, getPlayerStreams, PRNG} = require('pokemon-showdown');
const {RandomPlayerAI} = require('pokemon-showdown/dist/sim/tools/random-player-ai');

// pokemon-showdown 0.11.11 predates a few Champions data changes (Double Shock is a punching move, Slash and Meteor
// Assault powers, the abilities of five Mega formes). master-overlay.json carries only the fields that differ from the
// live Showdown master, applied before any set is built. ADR-077.
function applyMasterOverlay(format) {
	const overlay = JSON.parse(fs.readFileSync(path.join(__dirname, 'master-overlay.json'), 'utf8'));
	// An unknown format id silently falls back to plain Gen 9 data; every Champions claim here depends on the mod.
	if (Dex.forFormat(format).currentMod !== 'champions') throw new Error(`${format} does not use the champions mod`);
	const data = Dex.forFormat(format).data;
	for (const [id, change] of Object.entries(overlay.moves)) {
		if (!data.Moves[id]) continue;
		const {flags, ...rest} = change;
		Object.assign(data.Moves[id], rest);
		if (flags) data.Moves[id].flags = Object.fromEntries(flags.map(flag => [flag, 1]));
	}
	for (const [id, change] of Object.entries(overlay.species)) if (data.Pokedex[id]) Object.assign(data.Pokedex[id], change);
}

const STATS = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];
// Moves the inverse model excludes on purpose (crit mixtures, Substitute, move-copying), so a bot using them proves nothing.
const AVOID = new Set(['focusenergy', 'substitute', 'transform', 'mimic', 'sketch', 'copycat', 'metronome', 'mefirst',
	'assist', 'sleeptalk', 'mirrormove', 'snatch', 'conversion', 'haze', 'psychup']);
const PREFERRED = new Set(['protect', 'detect', 'helpinghand', 'tailwind', 'trickroom', 'reflect', 'lightscreen',
	'swordsdance', 'nastyplot', 'dragondance', 'calmmind', 'willowisp', 'thunderwave', 'fakeout', 'followme', 'ragepowder']);
const NATURES = Dex.natures.all().map(n => n.name);

function arg(name, fallback) {
	const index = process.argv.indexOf(`--${name}`);
	return index > -1 ? process.argv[index + 1] : fallback;
}

/** 66 points over six stats, at most 32 each; half the sets are concentrated like real investments. */
function randomPoints(prng) {
	const points = Object.fromEntries(STATS.map(stat => [stat, 0]));
	const weights = STATS.map(() => (prng.randomChance(1, 2) ? 1 : 1 + prng.random(8)) ** 2);
	for (let left = 66; left > 0;) {
		const total = weights.reduce((sum, w, i) => sum + (points[STATS[i]] < 32 ? w : 0), 0);
		let roll = prng.random(total);
		for (let i = 0; i < STATS.length; i++) {
			if (points[STATS[i]] >= 32) continue;
			roll -= weights[i];
			if (roll < 0) {
				points[STATS[i]]++;
				left--;
				break;
			}
		}
	}
	return points;
}

/** A spread shaped like a real investment: fast attacker, bulky body or bulky attacker; 66 points exactly. */
function metaPoints(prng, set, dex) {
	const moves = set.moves.map(m => dex.moves.get(m));
	const physical = moves.filter(m => m.category === 'Physical').length;
	const special = moves.filter(m => m.category === 'Special').length;
	const offense = physical >= special ? 'atk' : 'spa';
	const nature = dex.natures.get(set.nature);
	const slow = nature.minus === 'spe' || moves.some(m => m.id === 'trickroom');
	const attackers = physical + special;
	const kind = attackers === 0 ? 'bulky' : prng.sample(slow ? ['bulkyAttacker', 'bulky'] : ['fast', 'fast', 'bulkyAttacker', 'bulky']);
	const points = Object.fromEntries(STATS.map(stat => [stat, 0]));
	const bulk = prng.sample(['def', 'spd']);
	if (kind === 'fast') Object.assign(points, {spe: 32, [offense]: 32, hp: 2});
	else if (kind === 'bulkyAttacker') Object.assign(points, {hp: 32, [offense]: 32, [bulk]: 2});
	else Object.assign(points, {hp: 32, def: 32, spd: 2}, bulk === 'spd' ? {def: 2, spd: 32} : {});
	return points;
}

function teamFromSheet(prng, sheet, spreads, format, validator) {
	const dex = Dex.forFormat(format);
	return sheet.map(entry => {
		const set = {name: dex.species.get(entry.species).name, species: dex.species.get(entry.species).name,
			item: dex.items.get(entry.item).name, ability: dex.abilities.get(entry.ability).name,
			moves: entry.moves.map(m => dex.moves.get(m).name), nature: entry.nature, gender: entry.gender, level: 50,
			ivs: Object.fromEntries(STATS.map(s => [s, 31])), evs: {}};
		const meta = spreads === 'meta' || (spreads === 'mixed' && prng.randomChance(1, 2));
		set.evs = meta ? metaPoints(prng, set, dex) : randomPoints(prng);
		const problems = validator.validateSet(set, {});
		if (problems) throw new Error(`${entry.species}: ${problems.join('; ')}`);
		return set;
	});
}

/** Open team sheets of one recorded game: {p1: [...], p2: [...]}. */
function readSheets(file) {
	const log = JSON.parse(fs.readFileSync(file, 'utf8')).games[0].log;
	const sheets = {};
	for (const line of log.split('\n')) {
		const [, tag, side, packed] = line.match(/^\|(showteam)\|(p[12])\|(.*)$/) || [];
		if (!tag) continue;
		sheets[side] = packed.split(']').map(part => {
			const f = part.split('|');
			return {species: f[0], item: f[2], ability: f[3], moves: f[4].split(','), nature: f[5], gender: f[7]};
		});
	}
	return sheets;
}

function buildTeam(prng, format, validator) {
	const dex = Dex.forFormat(format);
	const species = dex.species.all().filter(s => s.exists && !s.isNonstandard && !s.forme && !s.battleOnly &&
		dex.species.getLearnsetData(s.id).learnset);
	const items = dex.items.all().filter(i => i.exists && !i.isNonstandard && !i.isPokeball && !i.zMove);
	const team = [];
	const used = {species: new Set(), item: new Set()};
	for (let attempts = 0; team.length < 6 && attempts < 4000; attempts++) {
		const mon = prng.sample(species);
		if (used.species.has(mon.baseSpecies)) continue;
		const learnable = Object.keys(dex.species.getLearnsetData(mon.id).learnset)
			.map(id => dex.moves.get(id)).filter(m => m.exists && !m.isNonstandard && !m.isZ && !m.isMax);
		const damaging = learnable.filter(m => m.category !== 'Status');
		if (damaging.length < 2) continue;
		const shuffled = list => { const copy = list.slice(); prng.shuffle(copy); return copy; };
		// Three attacks plus one status move: bots that mostly set up never expose damage to infer from.
		const status = learnable.filter(m => m.category === 'Status' && !AVOID.has(m.id));
		const preferred = status.filter(m => PREFERRED.has(m.id));
		const support = prng.sample(preferred.length ? preferred : status.length ? status : damaging);
		const attacks = shuffled(damaging).filter(m => m !== support).slice(0, 3);
		const moves = shuffled([...attacks, support]).map(m => m.name);
		const mega = dex.species.get(`${mon.name}-Mega`);
		const item = mega.exists && mega.requiredItem && !used.item.has(mega.requiredItem) && prng.randomChance(1, 3) &&
			![...used.item].some(name => dex.items.get(name).megaStone) ?
			mega.requiredItem : prng.sample(items).name;
		if (used.item.has(item)) continue;
		const abilities = Object.values(mon.abilities);
		const set = {name: mon.name, species: mon.name, item, ability: prng.sample(abilities), moves,
			nature: prng.sample(NATURES), evs: randomPoints(prng), ivs: Object.fromEntries(STATS.map(s => [s, 31])),
			level: 50, gender: ''};
		if (validator.validateSet(set, {})) continue;
		team.push(set);
		used.species.add(mon.baseSpecies);
		used.item.add(item);
	}
	if (team.length < 6) throw new Error('could not assemble a legal team');
	const problems = validator.validateTeam(team);
	if (problems) throw new Error(`team rejected: ${problems.join('; ')}`);
	return team;
}

async function playGame(format, names, teams, seed) {
	const streams = getPlayerStreams(new BattleStream());
	const lines = [];
	const reading = (async () => {
		for await (const chunk of streams.spectator) lines.push(chunk);
	})();
	const ai = (stream, offset) => new RandomPlayerAI(stream, {move: 0.9, mega: 0.5, seed: seed.map(n => n + offset)});
	void ai(streams.p1, 1).start();
	void ai(streams.p2, 2).start();
	void streams.omniscient.write([
		`>start ${JSON.stringify({formatid: format, seed})}`,
		`>player p1 ${JSON.stringify({name: names[0], team: Teams.pack(teams[0])})}`,
		`>player p2 ${JSON.stringify({name: names[1], team: Teams.pack(teams[1])})}`,
	].join('\n'));
	await reading;
	return lines.join('\n');
}

const winner = log => (log.match(/^\|win\|(.+)$/m) || [])[1];

async function series(index, base, format, outDir, sheets, spreads) {
	const prng = new PRNG([base, index, 7, 11]);
	const validator = TeamValidator.get(format);
	const players = ['SimAlpha', 'SimBeta'];
	const teams = players.map((_, i) => sheets ? teamFromSheet(prng, sheets[i], spreads, format, validator) :
		buildTeam(prng, format, validator));
	const wins = [0, 0];
	const games = [];
	const stamp = 1760000000 + index * 10000;
	for (let game = 0; wins[0] < 2 && wins[1] < 2 && game < 3; game++) {
		const swap = game > 0 && prng.randomChance(1, 2);
		const order = swap ? [1, 0] : [0, 1];
		const seed = [base + index, game + 1, prng.random(65535), prng.random(65535)];
		const raw = await playGame(format, order.map(i => players[i]), order.map(i => teams[i]), seed);
		let tick = 0; // the simulator stamps wall-clock time on several lines; keep them fixed and non-decreasing
		const log = raw.replace(/^\|t:\|\d+$/gm, () => `|t:|${stamp + game * 600 + tick++}`);
		const won = winner(log);
		if (!won) { game--; continue; } // a tie replays the game
		wins[players.indexOf(won)]++;
		games.push({id: `${format}-sim${base}x${index}g${game + 1}`, format: Dex.formats.get(format).name,
			players: order.map(i => players[i]), log, uploadtime: stamp + game * 600, views: 0,
			formatid: format, rating: null, private: 0, password: null});
	}
	const id = `sim${base}x${index}`;
	const truth = {};
	players.forEach((name, i) => {
		truth[name] = Object.fromEntries(teams[i].map(set => [set.species, {nature: set.nature, points: set.evs}]));
	});
	fs.writeFileSync(path.join(outDir, `bo3-public-${id}.json`), JSON.stringify(
		{source: `simulated ${format} (pokemon-showdown 0.11.11)`, replay_ids: games.map(g => g.id), games}));
	fs.writeFileSync(path.join(outDir, `bo3-truth-${id}.json`), JSON.stringify(
		{source: `${sheets ? `real open team sheets, ${spreads} ` : 'random '}native stat points drawn by the generator; scoring only`, level: 50, ivs: 31, teams: truth}));
	return {id, games: games.length, score: wins};
}

(async () => {
	const count = Number(arg('count', '1'));
	const base = Number(arg('seed', '1'));
	const format = arg('format', arg('teams', '') ? 'gen9championsvgc2026regmcbo3' : 'gen9championsvgc2026regmbbo3');
	const outDir = path.resolve(arg('out', '../../.cache/sim-corpus'));
	applyMasterOverlay(format);
	fs.mkdirSync(outDir, {recursive: true});
	const teamsDir = arg('teams', '');
	if (!teamsDir) {
		for (let i = 0; i < count; i++) console.log(JSON.stringify(await series(i, base, format, outDir)));
		return;
	}
	const variants = Number(arg('variants', '1'));
	const spreads = arg('spreads', 'mixed');
	const validator = TeamValidator.get(format);
	let index = 0;
	for (const file of fs.readdirSync(teamsDir).filter(f => /^bo3-public-showteam-.*\.json$/.test(f)).sort()) {
		const sheets = readSheets(path.join(teamsDir, file));
		try { // a dry run decides whether this simulator format can host the real teams at all
			[sheets.p1, sheets.p2].forEach(sheet => teamFromSheet(new PRNG([base, 0, 0, 0]), sheet, 'random', format, validator));
		} catch (error) {
			console.log(JSON.stringify({skipped: file, reason: String(error.message).slice(0, 200)}));
			continue;
		}
		for (let v = 0; v < variants; v++) {
			const info = await series(index++, base, format, outDir, [sheets.p1, sheets.p2], spreads);
			console.log(JSON.stringify({...info, from: file}));
		}
	}
})().catch(error => { console.error(error); process.exit(1); });
