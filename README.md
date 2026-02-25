# Foreman

Automates PRD-driven AI coding tasks using [ralph](https://github.com/ghuntley/open-ralph-wiggum) and Claude Code. Write PRDs, queue them, and Foreman runs them one at a time.

```
tasks/    ← write PRDs here
todo/     ← symlinks to queued PRDs
done/     ← completed archives
.ralph/   ← active ralph state (managed by ralph)
```

## Usage

**1. Write a PRD** in `tasks/prd-<slug>.md`, using `US-x` placeholders for user stories.
Or use the `/ralph-tui-prd` skill in Claude to generate one.

**2. Number it:**
```bash
foreman-prepare
# tasks/prd-cloud-sync.md → tasks/prd-07-cloud-sync.md
# US-x → US-007, US-008, ...
```

**3. Queue it:**
```bash
ln -s ../tasks/prd-07-cloud-sync.md todo/prd-07-cloud-sync.md
```

**4. Run foreman-run:**
```bash
foreman-run
```

Foreman loops indefinitely, picking up queued PRDs, launching ralph, polling until done, archiving the result, and moving to the next. Press **Ctrl+C** to stop — any running ralph job continues unaffected.
Runs in a server desktop's terminal or nohup.

## Options

### foreman-prepare
```
--dir DIR    Directory to scan (default: tasks)
```

### foreman-statusy
```
--done-dir DIR   Directory of completed archives (default: done)
```

### foreman-run
```
--poll-interval N     Seconds between todo/ scans (default: 5)
--status-interval N   Seconds between ralph --status polls (default: 30)
--max-iterations N    Max ralph iterations per PRD (default: 3)
--agent AGENT         Agent: claude-code (default), opencode, codex
--model MODEL         Model override, e.g. claude-sonnet-4-6
--no-allow-all        Disable auto-approval of tool permissions
-- ...                Extra flags passed directly to ralph
```

## Install

**1. Install foreman scripts** (adds `foreman-prepare`, `foreman-run`, `foreman-status` to PATH):

Linux/macOS:
```bash
bash install.sh
```
Windows:
```bat
install.bat
```

**2. Install ralph:**
```bash
bash open-ralph-wiggum-v1.2.1-with-verbose/install.sh
```

## Prerequisites

**Python 3.10+** and **bun**.

## Troubleshooting

**Ralph loops without completing** — manually resume:
```bash
ralph --file tasks/prd-07-cloud-sync.md --tasks --agent claude-code
```

**Stale active loop** — if `.ralph/ralph-loop.state.json` shows `active: true` but no ralph is running:
```bash
mv .ralph done/stale-ralph-manual/
```

**PRD skipped on restart** — remove it from `.todo_monitor.json`'s `processed` list to retry.
