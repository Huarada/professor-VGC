// Minimal echo server for tests/test_node_ipc_encoding.py. Reads one JSON
// line from stdin, writes it straight back with a UTF-8 field appended that
// contains bytes Windows' default cp1252 codepage cannot decode (this is the
// exact regression: NodeIpcClient used to decode Node's stdout with
// `text=True` alone, which falls back to the OS locale encoding on Windows
// instead of the UTF-8 Node actually writes).
'use strict';

const readline = require('readline');

const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on('line', (line) => {
  let payload;
  try {
    payload = JSON.parse(line);
  } catch (_e) {
    payload = {};
  }
  // The right double quotation mark (”, U+201D) encodes in UTF-8 as bytes
  // E2 80 9D — its last byte, 0x9D, is one of exactly five byte values
  // (0x81, 0x8D, 0x8F, 0x90, 0x9D) that are UNDEFINED in Windows' cp1252
  // codepage. This is not a hypothetical: it is the literal byte position
  // and value the real @smogon/calc description text crashed on.
  payload.unicode = 'a “smart quote” test — é×✓日鴞';
  process.stdout.write(JSON.stringify(payload) + '\n');
});
