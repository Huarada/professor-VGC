'use strict';

/**
 * calc_server.js
 *
 * IPC worker: reads ONE JSON request per line on stdin, writes ONE JSON
 * response per line on stdout. Node half of the polyglot boundary consumed by
 * src/adapters/calc/smogon_calc_adapter.py.
 *
 * Requests:
 *   {"cmd":"calc","gen":9,"attacker":{...},"defender":{...},"move":"Earthquake","field":{}}
 *   {"cmd":"speed","gen":9,"attacker":{...},"defender":{...}}
 *   {"cmd":"formeResolves","gen":9,"species":"Staraptor-Mega"}
 *   {"cmd":"ping"}
 * Responses:
 *   {"ok":true,"result":{...}}  |  {"ok":false,"error":"message"}
 */

const readline = require('readline');
const { calcDamage, compareSpeed, formeResolves } = require('./src/calcEngine');

function handle(request) {
  const cmd = request.cmd || 'calc';
  switch (cmd) {
    case 'ping':
      return { ok: true, result: { pong: true } };
    case 'calc':
      return {
        ok: true,
        result: calcDamage(
          request.gen || 9,
          request.attacker,
          request.defender,
          request.move,
          request.field
        ),
      };
    case 'speed':
      return {
        ok: true,
        result: compareSpeed(request.gen || 9, request.attacker, request.defender, request.field),
      };
    case 'formeResolves':
      return {
        ok: true,
        result: { resolves: formeResolves(request.gen || 9, request.species) },
      };
    default:
      return { ok: false, error: `Unknown cmd: ${cmd}` };
  }
}

function runSmoke() {
  const out = handle({
    cmd: 'calc',
    gen: 9,
    attacker: { species: 'Garchomp', level: 50, nature: 'Jolly', evs: { atk: 252, spe: 252 } },
    defender: { species: 'Sinistcha', level: 50, nature: 'Bold', evs: { hp: 252, def: 252 } },
    move: 'Earthquake',
    field: {},
  });
  process.stdout.write(JSON.stringify(out) + '\n');
}

function main() {
  if (process.argv.includes('--smoke')) {
    runSmoke();
    return;
  }
  const rl = readline.createInterface({ input: process.stdin, terminal: false });
  rl.on('line', (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let response;
    try {
      response = handle(JSON.parse(trimmed));
    } catch (err) {
      response = { ok: false, error: String(err && err.message ? err.message : err) };
    }
    process.stdout.write(JSON.stringify(response) + '\n');
  });
  rl.on('close', () => process.exit(0));
}

main();
