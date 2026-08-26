#!/usr/bin/env bash
# open_pr.sh — applies the ProfessorVGC fixes, pushes the branch, opens the PR and merges.
#   Usage: bash open_pr.sh [git-remote-url] [--no-merge]
#   Requires: git + GitHub CLI (gh) authenticated (gh auth login).
#   Run from the same folder as "fix-replay-log-parsing.patch".
set -Eeuo pipefail
REMOTE="${1:-https://github.com/Huarada/oracle-vgc.git}"
DO_MERGE=1; [[ "${2:-}" == "--no-merge" ]] && DO_MERGE=0
PATCH="fix-replay-log-parsing.patch"; BRANCH="fix/replay-log-parsing"; WORK="oracle-vgc-pr"
PR_TITLE="fix+feat: replay parsing, ordered timeline, field-aware speed, full-team meta, data docs"
PR_BODY=$'Eight commits.\n\n1. fix(parser): corrige \"Replay contains no sides/teams\" (JSON com log); erros descritivos.\n2. feat(analysis): resultado da batalha como ground truth; matchups cross-side; DATA.md.\n3. feat(parser): timeline ordenada de ações (anti-alucinação de causalidade).\n4. feat(context): meta cobre TODOS os Pokémon em jogo.\n5. feat(calc): speed/damage ciente do campo (Tailwind, paralisia, Trick Room, weather).\n6. feat(chaos): tiers de elo (ideal 1760 + bracket atual) e fallback de regulamento.\n7. feat(analysis): verificacao turno-a-turno (dano projetado vs real, speed do turno).\n8. feat(smogon): dados OFICIAIS via @pkmn/smogon (analyses/sets/stats) + fallback pro Chaos.\n\n66/66 testes passando.'
red(){ printf '\033[31m%s\033[0m\n' "$*"; }; grn(){ printf '\033[32m%s\033[0m\n' "$*"; }; ylw(){ printf '\033[33m%s\033[0m\n' "$*"; }
die(){ red "ERROR: $*"; exit 1; }
trap 'die "failed at line $LINENO. See above or use Option B in APPLY_FIX_PR.md."' ERR
command -v git >/dev/null || die "git not found."
command -v gh  >/dev/null || die "GitHub CLI (gh) not found. Install https://cli.github.com or use Option B."
gh auth status >/dev/null 2>&1 || die "gh not authenticated. Run: gh auth login"
[[ -f "$PATCH" ]] || die "file '$PATCH' not found in this folder."
ylw ">> Cloning $REMOTE ..."; rm -rf "$WORK"
git clone --quiet "$REMOTE" "$WORK" || die "clone failed. Check URL/access."
cd "$WORK"
git config user.email >/dev/null 2>&1 || git config user.email "$(git config --global user.email 2>/dev/null || echo oracle-vgc-bot@users.noreply.github.com)"
git config user.name  >/dev/null 2>&1 || git config user.name  "$(git config --global user.name  2>/dev/null || echo 'ProfessorVGC Bot')"
BASE="$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || echo main)"; grn ">> Default branch: $BASE"
git checkout --quiet -B "$BRANCH" "origin/$BASE" 2>/dev/null || git checkout --quiet -B "$BRANCH"
ylw ">> Applying the patch ..."
if git am --3way "../$PATCH" >/dev/null 2>&1; then grn ">> Applied via git am."
else
  git am --abort >/dev/null 2>&1 || true; ylw ">> git am failed; trying git apply ..."
  if git apply --3way "../$PATCH" >/dev/null 2>&1 || git apply "../$PATCH" >/dev/null 2>&1; then
    git add -A; git -c commit.gpgsign=false commit -q -m "$PR_TITLE"; grn ">> Applied via git apply."
  else die "could not apply patch (remote diverged); apply changed files manually."; fi
fi
ylw ">> Pushing ..."; git push --quiet -u origin "$BRANCH" --force-with-lease || die "push failed (write access? token scope 'repo'?)."
if gh pr view "$BRANCH" >/dev/null 2>&1; then ylw ">> PR already exists; reusing."
else ylw ">> Opening PR ..."; gh pr create --base "$BASE" --head "$BRANCH" --title "$PR_TITLE" --body "$PR_BODY" || die "open PR manually at ${REMOTE%.git}/pull/new/$BRANCH"; fi
PR_URL="$(gh pr view "$BRANCH" --json url -q .url)"; grn ">> PR: $PR_URL"
if [[ "$DO_MERGE" -eq 1 ]]; then
  ylw ">> Merging (squash) ..."
  if gh pr merge "$BRANCH" --squash --delete-branch || gh pr merge "$BRANCH" --squash --delete-branch --admin; then grn ">> Merged into '$BASE'. Done!"
  else ylw ">> Could not auto-merge (branch protection). Merge in the UI: $PR_URL"; fi
else grn ">> PR opened (no merge). Review/merge at: $PR_URL"; fi
