'use strict';

// Read-only, offline inventory of the installed Champions calculator policy.
const { coverage, rules } = require('../src/candidateSupport');
const entries = coverage();
const damaging = entries.filter(entry => ['Physical', 'Special'].includes(entry.category));
const report = {
  engine_version: rules().engine_version,
  catalog_moves: entries.length,
  damaging_moves: damaging.length,
  conditionally_supported_damage_moves: damaging.filter(entry => entry.supported).length,
  supported_multi_hit_moves: entries.filter(entry => entry.supported && entry.multi_hit).length,
  meaning: 'Catalog eligibility only. Missing runtime state, items, abilities and budgets can still exclude a hit. Not damage accuracy.',
  excluded_damage_moves: damaging.filter(entry => !entry.supported),
};
if (process.argv.includes('--all')) report.catalog = entries;
process.stdout.write(JSON.stringify(report, null, 2) + '\n');
