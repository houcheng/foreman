# Foreman

Foreman is a two-script utility that automates the execution of PRD-driven AI coding tasks
using [ralph](https://github.com/ghuntley/open-ralph-wiggum) and Claude Code.

You write PRDs. Foreman assigns numbers, queues them, runs them one at a time, and archives
the results.

```
tasks/          ← you write PRDs here
todo/           ← symlinks to PRDs queued for execution
done/           ← completed archives (ralph state + stream logs)
.ralph/         ← active ralph loop state (managed by ralph)
```

---

## Scripts

| Script | Purpose |
|---|---|
| `foreman-prepare.py` | Assigns `prd-NN-` prefixes and `US-x` numbers to user stories |
| `foreman-run.py` | Watches `todo/`, drives ralph, archives results |

---

## Standard Operating Procedure

### Step 1 — Write a PRD

Create a markdown file in `tasks/` named `prd-<slug>.md` (no number yet).

```
tasks/prd-cloud-sync.md
```

Use plain markdown. Mark each user story with a `US-x` placeholder:

```markdown
# Cloud Sync Feature

## US-x  Upload books to Dropbox
Allow the user to back up their EPUB library to Dropbox.

## US-x  Restore from backup
Allow the user to restore their library from a Dropbox backup.
```
### Step 1 - User claude /ralph-tui-prd skill

In claude, run /ralph-tui-prd skill and input this

```
write a script xxx
ouput the prd md file into tasks/
```


### Step 2 — Run foreman-prepare

```bash
python foreman-prepare.py
```

This scans `tasks/` and for each un-numbered PRD:
- Renames it with the next available `prd-NN-` prefix
- Replaces every `US-x` placeholder with a globally unique sequential number

```
tasks/prd-cloud-sync.md  →  tasks/prd-07-cloud-sync.md
US-x  →  US-007, US-008, ...
```

Numbers are assigned across all existing PRDs so user story IDs are globally unique.

### Step 3 — Queue the PRD

Create a symlink in `todo/` pointing to the numbered PRD:

```bash
ln -s ../tasks/prd-07-cloud-sync.md todo/prd-07-cloud-sync.md
```

You can queue multiple PRDs at once. `foreman-run.py` processes them one at a time
in ascending numeric order.

### Step 4 — Start foreman-run

Run this from the project root (the directory that contains `tasks/`, `todo/`, `done/`):

```bash
python foreman-run.py
```

Leave it running. It loops indefinitely, picking up new symlinks as they appear.

**What it does automatically (steps 4–8):**

4. Detects the symlink in `todo/` and launches ralph:
   ```
   ralph --file todo/prd-07-cloud-sync.md --tasks --agent claude-code \
         --max-iterations 3 --log-file prd-07-cloud-sync-stream-<ts>.log
   ```
5. Polls `ralph --status --tasks` every 30 seconds, logging progress.
6. On completion, archives `.ralph/` state:
   ```
   done/prd-07-cloud-sync-ralph-<ts>/
   ```
7. Moves the ralph stream log:
   ```
   done/prd-07-cloud-sync-stream-<ts>.log
   ```
8. Removes the `todo/` symlink and waits for the next PRD.

Press **Ctrl+C** to stop the monitor. Any ralph job currently running is **not killed** —
it keeps running. Restart `foreman-run.py` to resume tracking it.

---

## Options

### foreman-prepare.py

```
--dir DIR    Directory to scan for PRD files (default: tasks)
```

### foreman-run.py

```
--poll-interval N     Seconds between todo/ scans (default: 5)
--status-interval N   Seconds between ralph --status polls (default: 30)
--max-iterations N    Max ralph iterations per PRD (default: 3)
--agent AGENT         AI agent: claude-code (default), opencode, codex
--model MODEL         Model override, e.g. claude-sonnet-4-6
--term                Launch ralph in a new gnome-terminal window for live output
--no-allow-all        Disable auto-approval of tool permissions (enables prompts)
--ralph-log-file PATH Override the auto-generated stream log path
-- ...                Pass any extra flags directly to ralph
```

---

## Folder layout after a completed run

```
done/
  prd-07-cloud-sync-ralph-20260224-143000/   ← archived .ralph/ state
    ralph-loop.state.json
    ralph-tasks.md
    ralph-history.json
    ...
  prd-07-cloud-sync-stream-20260224-143000.log  ← full claude output (plain text)
```

---

## Prerequisites

**Python 3.10+** and **ralph (patched fork)**.

The patched ralph fork adds `--log-file` support and fixes COMPLETE detection for
claude-code's stream-json output. It is in `open-ralph-wiggum/` in this repo.

Install it:

```bash
cd open-ralph-wiggum-logfile
bun install
bun link          # makes 'ralph' available in PATH
ralph --version   # must end with -logfile, e.g. 1.2.2-logfile
```

`foreman-run.py` will refuse to start if the wrong ralph version is detected.

---

## Troubleshooting

**`ERROR: ralph version '1.2.2' is not the patched fork`**
Run `cd open-ralph-wiggum && bun install && bun link` and verify `ralph --version`
ends with `-logfile`.

**Ralph loops without completing**
Check `done/prd-NN-...-stream-*.log` for the last iteration output.
Manually resume with:
```bash
ralph --file tasks/prd-07-cloud-sync.md --tasks --agent claude-code
```

**`ralph state file shows an active loop`**
A previous ralph run left `.ralph/ralph-loop.state.json` with `active: true`.
If no ralph process is actually running, back up and remove `.ralph/`:
```bash
mv .ralph done/stale-ralph-manual/
```

**PRD is skipped on restart**
`foreman-run.py` persists state in `.todo_monitor.json`. If a PRD name appears in
`processed`, it will not be re-queued. Remove the entry manually to retry.
