# Foreman

Automates AI coding tasks using [ralph](https://github.com/ghuntley/open-ralph-wiggum) and Claude Code. Supports two task modes:

- **PRD mode** — structured user-story PRDs executed via ralph
- **Todo/Plan mode** — freeform task files executed directly by claude (two-pass: implement + verify)

```
tasks/    ← write PRDs and todo files here
todo/     ← symlinks to queued tasks
done/     ← completed archives
.ralph/   ← active ralph state (managed by ralph)
```

---

## Quick Start: Todo mode (simplest)

```bash
foreman-add
# → prompts for title and requirements
# → creates tasks/todo-my-task.md and todo/todo-my-task.md symlink

foreman-run
# → detects todo-my-task.md, runs two claude passes, archives result
```

## Quick Start: PRD mode (structured)

**1. Write a PRD** in `tasks/prd-<slug>.md`, using `US-1`, `US-2` placeholders.
Or use the `/ralph-tui-prd` skill in Claude to generate one.

**2. Number and queue it:**
```bash
foreman-prepare --link
# tasks/prd-cloud-sync.md → tasks/prd-07-cloud-sync.md
# US-1 → US-007, US-008, ...
# creates todo/prd-07-cloud-sync.md symlink
```

**3. Run foreman-run:**
```bash
foreman-run
```

Foreman loops indefinitely, picking up queued tasks one at a time.
Press **Ctrl+C** to stop — any running job continues unaffected.

---

## Commands

### foreman-add

Interactively creates a todo task and queues it:

```bash
foreman-add                          # fully interactive
foreman-add -f plan-my-feature.md   # seed from an existing plan file
```

Prompts for job title and requirements, then writes `tasks/todo-<slug>.md`
and creates `todo/todo-<slug>.md` symlink.

### foreman-run

```
--poll-interval N     Seconds between todo/ scans (default: 5)
--status-interval N   Seconds between ralph --status polls (default: 30)
--max-iterations N    Max ralph iterations per PRD (default: 5)
--agent AGENT         Agent: claude-code (default), opencode, codex
--model MODEL         Model override, e.g. claude-sonnet-4-6
--no-allow-all        Disable auto-approval of tool permissions
-- ...                Extra flags passed directly to ralph
```

### foreman-prepare

```
--dir DIR    Directory to scan (default: tasks)
--link       Also create todo/ symlinks for newly numbered PRDs
```

### foreman-status

```
--done-dir DIR   Directory of completed archives (default: done)
```

Shows completed ralph runs (tasks checked off) and completed todo/plan runs (status).

---

## How todo/plan mode works

When `foreman-run` finds a `todo-*.md` or `plan-*.md` in `todo/`, instead of launching ralph it runs two claude passes directly:

```
Pass 1:  claude --print "read the FILE and implement it.
                         When done output <prompt>COMPLETE</prompt>."

Pass 2:  claude --print "read FILE listing what we implemented.
                         Check if done, output <prompt>COMPLETE</prompt>."
```

Results are archived to `done/{name}-claude-<ts>/` with a `status.md` and the full session log.

---

## Install

**Linux/macOS:**
```bash
bash install.sh
```

**Windows:**
```bat
install.bat
```

Installs: `foreman-add`, `foreman-prepare`, `foreman-run`, `foreman-status`

**Install ralph:**
```bash
bash open-ralph-wiggum-v1.2.1-with-verbose/install.sh
```

## Prerequisites

**Python 3.10+** and **bun** (for ralph).

---

## Troubleshooting

**Ralph loops without completing** — manually resume:
```bash
ralph --file tasks/prd-07-cloud-sync.md --tasks --agent claude-code
```

**Stale active loop** — if `.ralph/ralph-loop.state.json` shows `active: true` but no ralph is running:
```bash
mv .ralph done/stale-ralph-manual/
```

**Todo task didn't complete** — check the log in `done/todo-<name>-claude-<ts>/`:
```bash
cat done/todo-my-task-claude-20260227-143000/todo-my-task-20260227-143000.log
```
