'use strict';

/**
 * smogon_dex_server.js
 *
 * IPC worker (one JSON request per line -> one JSON response per line),
 * consumed by src/adapters/smogon/smogon_dex_adapter.py.
 *
 * Requests:
 *   {"cmd":"species","gen":9,"name":"Garchomp"}
 *   {"cmd":"analyses","gen":9,"name":"Garchomp","format":"gen9vgc2024regh"}
 *   {"cmd":"sets","gen":9,"name":"Garchomp","format":"..."}
 *   {"cmd":"stats","gen":9,"name":"Garchomp","format":"..."}
 *   {"cmd":"ping"}
 * Responses: {"ok":true,"result":...} | {"ok":false,"error":"..."}
 */

const readline = require('readline');
const dex = require('./src/smogonDex');

async function handle(req) {
  const cmd = req.cmd || 'species';
  switch (cmd) {
    case 'ping':
      return { ok: true, result: { pong: true } };
    case 'species':
      return { ok: true, result: dex.speciesInfo(req.gen || 9, req.name) };
    case 'analyses':
      return { ok: true, result: await dex.analyses(req.gen || 9, req.name, req.format) };
    case 'sets':
      return { ok: true, result: await dex.sets(req.gen || 9, req.name, req.format) };
    case 'stats':
      return { ok: true, result: await dex.stats(req.gen || 9, req.name, req.format) };
    default:
      return { ok: false, error: `Unknown cmd: ${cmd}` };
  }
}

function main() {
  const rl = readline.createInterface({ input: process.stdin, terminal: false });
  rl.on('line', async (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let response;
    try {
      response = await handle(JSON.parse(trimmed));
    } catch (err) {
      response = { ok: false, error: String(err && err.message ? err.message : err) };
    }
    process.stdout.write(JSON.stringify(response) + '\n');
  });
  rl.on('close', () => process.exit(0));
}

main();
