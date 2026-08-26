# ProfessorVGC — abrir PR e dar merge (8 commits)

`fix-replay-log-parsing.patch` (**8 commits**). Código em inglês. **66/66 testes.**

1. **fix(parser)** — `Replay contains no sides/teams` (JSON com `log`) + erros descritivos.
2. **feat(analysis)** — resultado da batalha como ground truth; matchups cross-side; `DATA.md`.
3. **feat(parser)** — timeline ordenada de ações (anti-alucinação de causalidade).
4. **feat(context)** — meta cobre todos os Pokémon em jogo.
5. **feat(calc)** — speed/damage ciente do campo (Tailwind, paralisia, Trick Room, weather).
6. **feat(chaos)** — tiers de elo (ideal 1760 + bracket atual) + fallback de regulamento + formas.
7. **feat(analysis)** — **verificação turno-a-turno** (dano projetado vs real, speed do turno).
8. **feat(smogon)** — **dados OFICIAIS via `@pkmn/smogon`**: `analyses` (estratégia em linguagem
   natural), `sets` e `stats` (sugestões de melhoria/sinergia quando o jogador pede). Fica atrás
   de `PROFESSORVGC_USE_SMOGON_DEX=true`, com **fallback automático pro Chaos** se a rede/dado faltar.

> Não pude abrir o PR pela sessão (sem `gh`/token; `api.github.com` bloqueado).

## Opção A — GitHub CLI
```bash
bash open_pr.sh https://github.com/Huarada/oracle-vgc.git      # (--no-merge só abre)
```
## Opção B — manual
```bash
git clone https://github.com/Huarada/oracle-vgc.git && cd oracle-vgc
git checkout -b fix/replay-log-parsing
git am ../fix-replay-log-parsing.patch
git push -u origin fix/replay-log-parsing    # abra o PR e clique Merge
```

## Ativar os dados oficiais do Smogon
```bash
cd node_calc && npm install          # instala @pkmn/dex @pkmn/data @pkmn/smogon
# no .env:
PROFESSORVGC_USE_SMOGON_DEX=true
```
`analyses/sets/stats` precisam de rede em runtime (a sua máquina tem; meu sandbox não).
Detalhes em **DATA.md**.

## Testar
```bash
pip install -r requirements.txt && (cd node_calc && npm install)
pytest -q                 # 66 passed
streamlit run src/ui/app.py
```
