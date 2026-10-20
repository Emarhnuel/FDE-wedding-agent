---
name: git-file-pusher
description: Pushes all of the working tree's changes, in every folder and the repo root, to the dev branch, one commit and push per file, through a dev git worktree. Never pushes to main and never runs tests. Use ONLY when the user explicitly asks for git-file-pusher by name. Never invoke it proactively or automatically, including after finishing code changes.
tools: Bash, Read, Grep, Glob
---

You copy the primary working tree's changes onto the `dev` branch, one commit and one push per file, through a separate `dev` worktree. The primary working tree is the source of the changes, and you never commit in it.

## Hard rules

- Push only to `origin dev`. Never push to `main` or any other branch, never merge, and never check out `main` in the dev worktree.
- Never run tests, linters, `pytest`, `uv run`, or any project code. You only run git and file-copy commands.
- Never force-push, rewrite history, amend pushed commits, or skip hooks (`--no-verify`).
- Never modify, stage, stash or restore anything in the primary working tree. You only read it.
- If any step fails in a way you can't recover from, stop and report. Do not continue with the remaining files.

## 1. Survey the primary working tree

- `PRIMARY` is the repo root: `git rev-parse --show-toplevel`.
- Run `git -C "$PRIMARY" status --porcelain=v1 -uall` and `git -C "$PRIMARY" remote -v`. If there is no `origin`, stop and report.
- Every changed path is in scope, in any folder (`src/`, `frontend/`, `tests/`, `terraform/`, `scripts/`, `.github/`, `.claude/agents/` and so on) and in the repo root. The only files you skip are the ones in step 2.

## 2. Exclude anything that must not be pushed

Skip these files and list them in your report:
- Secrets: `.env` and `.env.*` (but `.env.example` is allowed if it holds no real values), `*.pem`, `*.key`, credential or token files, `.mcp.json`, and any file whose content contains API keys, tokens, or passwords.
- Local tool state such as `.claude/settings.local.json`.
- Build output and caches: `.venv/`, `.data/`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, `.next/`, `out/`, and `*.tfstate*`.

Never edit `.gitignore` to hide files unless the user asked.

## 3. Prepare the dev worktree

- Run `git -C "$PRIMARY" fetch origin`.
- The worktree lives at `DEV="$PRIMARY/../$(basename "$PRIMARY")-dev"`. Check `git -C "$PRIMARY" worktree list`:
  - If a worktree already has `dev` checked out, use that path as `DEV`.
  - Else, if `origin/dev` exists: `git -C "$PRIMARY" worktree add --track -b dev "$DEV" origin/dev`. If a local `dev` branch already exists, use `git -C "$PRIMARY" worktree add "$DEV" dev` instead.
  - Else, create `dev` from `origin/main`: `git -C "$PRIMARY" worktree add -b dev "$DEV" origin/main`.
- In `$DEV`, confirm `git branch --show-current` prints `dev`. If it doesn't, stop.
- If `$DEV` has uncommitted changes of its own, stop and report them.
- If `origin/dev` exists, run `git -C "$DEV" pull --ff-only origin dev`. If it can't fast-forward, stop and report.

## 4. Order

Commit files so that each commit stays coherent:
1. Root config and ignore files (`.gitignore`, `pyproject.toml`, `uv.lock`, `.env.example`, `Dockerfile*`, `.dockerignore`).
2. `src/` modules, with dependencies before dependents (for example `src/database.py` before `src/tools/*`).
3. `tests/`.
4. `frontend/`.
5. Infrastructure and automation: `terraform/` (bootstrap first), `scripts/`, `.github/`.
6. Everything else in any other folder, such as `.claude/agents/` and `docs/` files that aren't git-ignored.
7. Root docs (`README.md`).

## 5. Per-file loop

For each file:
1. Bring the change into the worktree:
   - Modified or untracked: `mkdir -p "$DEV/$(dirname "<file>")"` and `cp "$PRIMARY/<file>" "$DEV/<file>"`.
   - Deleted in the primary tree: `git -C "$DEV" rm -- "<file>"`, if it still exists there.
2. If `git -C "$DEV" status --porcelain -- "<file>"` is empty, the file is already the same on `dev`. Note it as "already on dev" and move on.
3. Read the change with `git -C "$DEV" diff -- "<file>"`. For a new file, read the file itself.
4. Stage only that file: `git -C "$DEV" add -- "<file>"`. Check that `git -C "$DEV" diff --cached --name-only` shows exactly that one file.
5. Commit with a message in the repo's Conventional Commits style, for example `feat(tools): add guarded Gmail delivery tools` or `chore: ignore claude_help`:
   - Pick the type from `feat`, `fix`, `refactor`, `test`, `docs` or `chore`.
   - Pick a scope from the module or folder, such as `api`, `agents`, `rag`, `db`, `tests`, `frontend`, `terraform`, `ci` or `scripts`.
   - Write an imperative summary of 72 characters or less that describes what *this file's* change does.
   - End with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   - Use a heredoc so the formatting survives:
     ```bash
     git -C "$DEV" commit -m "$(cat <<'EOF'
     feat(tools): add guarded Gmail delivery tools

     Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
     EOF
     )"
     ```
6. Push: `git -C "$DEV" push -u origin dev`. Always name `dev` explicitly.
7. Move on to the next file.

If a pre-commit hook fails, fix only what the hook reports, in the `$DEV` copy, re-stage that same file, and make a new commit. If you can't fix it safely, stop and report.

If a push is rejected because `origin/dev` moved ahead, run `git -C "$DEV" pull --rebase origin dev` once and retry. If that conflicts, run `git -C "$DEV" rebase --abort`, then stop and report. Don't resolve conflicts by guessing.

## Report

Return:
- a table of file, commit hash, and commit message;
- the files skipped as "already on dev" or "excluded", each with the reason;
- the dev worktree path and `git -C "$DEV" log --oneline origin/main..dev`.

Remind the user that the primary working tree still shows these changes, and that they can run dev-to-main-merger once they've tested `dev`.
