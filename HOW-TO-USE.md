# How to Use Foreman

Foreman supports two task modes. Choose the one that fits your workflow.

---

## Mode A — Todo/Plan (direct claude, freeform)

Use this for quick tasks, exploratory work, or when you don't need the full
PRD/user-story structure.

```
foreman-add
       │
       ▼  prompts: title, requirements
       │  writes:  tasks/todo-<slug>.md
       │  creates: todo/todo-<slug>.md  (symlink)
       │
       ▼
foreman-run (watching todo/)
  - detects todo-<slug>.md
  - Pass 1: claude --print "read the FILE and implement it.
                             When done output <prompt>COMPLETE</prompt>."
  - waits for COMPLETE signal in output
  - Pass 2: claude --print "read FILE listing what we've implemented.
                             Check if done, output <prompt>COMPLETE</prompt>."
  - archives log → done/todo-<slug>-claude-<ts>/
  - removes todo/ symlink, picks up next task
```

### Steps

**Step 1 — Create and queue a task**

```bash
foreman-add
```

Example session:
```
=== Foreman Task Creator ===
Job title: Add CSV export to the reports page
Requirements (enter a blank line to finish):
Export the current filtered view as UTF-8 CSV
Include all visible columns with headers
Filename should be reports-YYYY-MM-DD.csv

Created:  tasks/todo-add-csv-export-to-the-reports-page.md
Queued:   todo/todo-add-csv-export-to-the-reports-page.md → ../tasks/todo-...md
```

Or seed from an existing plan file:
```bash
foreman-add -f plan-csv-export.md
```

**Step 2 — Run foreman-run**

```bash
foreman-run
```

Foreman detects the symlink and runs claude twice (implement then verify).
The full output is logged to `done/todo-<slug>-claude-<ts>/<slug>.log`.

**Step 3 — Check status**

```bash
foreman-status
```

Shows completed runs. For todo tasks, shows the `status.md` (COMPLETE / INCOMPLETE).

To inspect the full log:
```bash
cat done/todo-add-csv-export-to-the-reports-page-claude-20260227-143000/*.log
```

---

## Mode B — PRD (ralph-driven, structured user stories)

Use this for larger features where you want to break work into user stories
that are tracked and resumed automatically.

```
You write a PRD
       │
       ▼
foreman-prepare --link
  - renames tasks/prd-my-feature.md → tasks/prd-07-my-feature.md
  - renumbers US-x placeholders → US-007, US-008, ...
  - creates todo/prd-07-my-feature.md symlink
       │
       ▼
foreman-run (watching todo/)
  - detects prd-07-my-feature.md
  - launches: ralph --file todo/prd-07-my-feature.md --tasks --agent claude-code
       │
       ▼
ralph (loop driver)
  - iteration 1: sends PRD to claude-code, asks it to write .ralph/ralph-tasks.md
  - iteration 2+: reads tasks, tells claude-code which task is next
  - detects <promise>READY_FOR_NEXT_TASK</promise> to advance
  - detects <promise>COMPLETE</promise> to stop
       │
       ▼
claude-code (the AI, doing real work)
  - reads your PRD on iteration 1
  - writes .ralph/ralph-tasks.md:
        - [ ] Implement US-007 upload to Dropbox
        - [ ] Implement US-008 restore from backup
  - works through tasks one by one, marking [/] then [x]
       │
       ▼
foreman-run (on completion)
  - archives .ralph/ → done/prd-07-my-feature-ralph-<ts>/
  - removes todo/ symlink, picks up next PRD
```

**Key point:** Ralph never parses your PRD. It passes the whole file as a prompt to
claude-code. Claude-code reads it, understands the user stories, and writes the task
list in `.ralph/ralph-tasks.md`. Ralph only tracks the checkboxes.

### Steps

**Step 1 — Write your PRD**

Create `tasks/prd-my-feature.md` (must start with `prd-`, no number yet).
Use `US-1`, `US-2`, ... as placeholders — foreman-prepare assigns globally unique IDs.

```markdown
# My Feature

## US-1  User can upload files
Describe the story here.

## US-2  User can delete uploaded files
Describe the story here.
```

Or generate it with the Claude skill:
```
/ralph-tui-prd
```

**Step 2 — Prepare and queue**

```bash
foreman-prepare --link
```

This does everything in one step:
- Renames `tasks/prd-my-feature.md` → `tasks/prd-07-my-feature.md`
- Replaces `US-1`, `US-2` with globally unique IDs (e.g. `US-007`, `US-008`)
- Creates `todo/prd-07-my-feature.md` symlink

**Step 3 — Run foreman-run**

```bash
foreman-run
```

Leave it running. It picks up todo/ symlinks automatically, processing one at a time.
You can queue multiple PRDs before or during the run.

**Monitor progress (separate terminal, from project root)**

```bash
ralph --status --tasks    # loop state + current task list
cat .ralph/ralph-tasks.md # raw task file
```

**After a PRD completes**

`foreman-run` automatically archives `.ralph/` to `done/`:

```
.ralph/  →  done/prd-07-my-feature-ralph-20260225-143000/
```

### Inject hints mid-run

```bash
ralph --add-context "The upload endpoint is in src/api/upload.ts"
ralph --add-task "Also add a progress bar"
```

### Stop

Press `Ctrl+C` in the foreman-run terminal. The running ralph job is not killed —
restart `foreman-run.py` to resume tracking it.

---

## Skip foreman-run (run ralph directly)

```bash
ralph --file tasks/prd-07-my-feature.md --tasks --agent claude-code --max-iterations 5
```

Useful when iterating on a single PRD without the full foreman pipeline.

---

## Appendix

### A. Directory layout

```
tasks/
  prd-07-cloud-sync.md         ← numbered PRD
  todo-add-csv-export.md       ← todo task (created by foreman-add)
  plan-refactor-auth.md        ← plan file (manually created)

todo/
  prd-07-cloud-sync.md         ← symlink → ../tasks/prd-07-cloud-sync.md
  todo-add-csv-export.md       ← symlink → ../tasks/todo-add-csv-export.md

done/
  prd-07-cloud-sync-ralph-20260225-143000/    ← archived .ralph/ state
    ralph-tasks.md
    ralph-loop.state.json
    ...
  todo-add-csv-export-claude-20260227-100000/ ← archived claude session
    status.md                                 ← COMPLETE or INCOMPLETE
    todo-add-csv-export-20260227-100000.log   ← full claude output

.ralph/
  ralph-loop.state.json   ← active ralph state (cleared on archive)
  ralph-tasks.md
```

### B. How foreman-run guards against starting a duplicate ralph job

1. **`is_ralph_active()`** — reads `.ralph/ralph-loop.state.json` and checks
   `"active": true`. If true, foreman-run skips the PRD and retries next poll.

2. **`backup_stale_ralph()`** — if `.ralph/` exists but `active` is false
   (crashed or killed run), moves the whole directory to `done/stale-ralph-<ts>/`
   before starting fresh.

### C. The "⏳ working..." heartbeat (PRD mode only)

```
⏳ working... elapsed 2:40 · last activity 0:16 ago
```

Printed by ralph every 10 seconds when claude-code produces no output.
It means claude-code is thinking or doing something without printing.
If "last activity" climbs past several minutes, the agent may be stuck.

### D. US placeholder format summary (PRD mode)

| Format | Works with foreman-prepare? | Notes |
|---|---|---|
| `US-x` | NO | `x` is not a digit |
| `US-1` | YES | simplest |
| `US-001` | YES | also fine, gets renumbered anyway |
| `## US-003` | YES | heading level doesn't matter |

Use `US-1`, `US-2`, ... as your placeholders when writing a new PRD.

### E. Troubleshooting

**Ralph loops without completing** — manually resume:
```bash
ralph --file tasks/prd-07-cloud-sync.md --tasks --agent claude-code
```

**Stale active loop** — if `.ralph/ralph-loop.state.json` shows `active: true`
but no ralph is running:
```bash
mv .ralph done/stale-ralph-manual/
```

**Todo task shows INCOMPLETE** — inspect the log:
```bash
cat done/todo-<name>-claude-<ts>/<name>.log
```
The log contains both pass 1 and pass 2 output separated by `===` headers.
Look for the COMPLETE signal near the end of each pass.

**Todo task symlink left behind** — if foreman-run was interrupted during a todo job,
the symlink stays in `todo/`. On restart, foreman-run will attempt the job again
from pass 1.
