# How to Use Foreman

## Overview: What Happens Under the Hood

```
You write a PRD
       │
       ▼
foreman-prepare.py --link
  - renames tasks/prd-my-feature.md → tasks/prd-07-my-feature.md
  - renumbers US-x placeholders → US-007, US-008, ...
  - (with --link) creates todo/prd-07-my-feature.md → symlink
       │
       ▼
foreman-run.py (watching todo/)
  - detects the symlink
  - launches:  ralph --file todo/prd-07-my-feature.md --tasks --agent claude-code
       │
       ▼
ralph (loop driver)
  - iteration 1: sends the entire PRD as a prompt to claude-code
                 tells it: "create .ralph/ralph-tasks.md with a - [ ] task list"
  - iteration 2+: reads .ralph/ralph-tasks.md, tells claude-code which task is next
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
  - outputs the promise signals when done
       │
       ▼
foreman-run.py (on completion)
  - archives .ralph/ → done/prd-07-my-feature-ralph-<ts>/
  - moves stream log → done/prd-07-my-feature-stream-<ts>.log
  - removes todo/ symlink, waits for next PRD
```

**Key point:** Ralph never parses your PRD. It passes the whole file as a prompt to
claude-code. Claude-code reads it, understands the user stories, and writes the task
list in `.ralph/ralph-tasks.md`. Ralph only tracks the checkboxes.

---

## Steps

### Step 1 — Write your PRD

Create `tasks/prd-my-feature.md` (no number yet, must start with `prd-`).

Use `US-1`, `US-2`, ... (any digit) as placeholders — foreman-prepare will assign
globally unique numbers later.

```markdown
# My Feature

## US-1  User can upload files
Describe the story here.

## US-2  User can delete uploaded files
Describe the story here.
```

Or generate it with Claude:

```
/ralph-tui-prd

Please describe the feature you want to build...
[answer questions]

Write the PRD to tasks/prd-my-feature.md
```

### Step 2 — Prepare and queue

```bash
python foreman-prepare.py --link
```

This does everything in one step:
- Renames `tasks/prd-my-feature.md` → `tasks/prd-07-my-feature.md`
- Replaces `US-1`, `US-2` with globally unique IDs (e.g. `US-007`, `US-008`)
- Creates `todo/prd-07-my-feature.md` symlink (because of `--link`)

### Step 3 — Start the runner

```bash
python foreman-run.py
```

Leave it running. It picks up todo/ symlinks automatically and processes them
one at a time. You can queue multiple PRDs before or during the run.

### Monitor progress (separate terminal, from project root)

```bash
ralph --status --tasks    # show loop state + current task list
ralph --list-tasks        # task list only
cat .ralph/ralph-tasks.md # raw task file
```

### After a PRD completes

`foreman-run.py` automatically archives `.ralph/` to `done/`:

```
.ralph/  →  done/prd-07-my-feature-ralph-20260225-143000/
```

So `ralph --status --tasks` may show the completed tasks right after finishing,
but the next call finds nothing because `.ralph/` has been moved. The tasks are
not deleted — find them in `done/`:

```bash
cat done/prd-07-my-feature-ralph-20260225-143000/ralph-tasks.md
```

### How foreman-run guards against starting a duplicate ralph job

There is no lock file. Ralph does not create one. The guard is the state JSON:

1. **`is_ralph_active()`** — reads `.ralph/ralph-loop.state.json` and checks
   `"active": true`. If true, foreman-run skips the PRD and retries next poll.

2. **`backup_stale_ralph()`** — if `.ralph/` exists but `active` is false
   (crashed or killed run), moves the whole directory to `done/stale-ralph-<ts>/`
   before starting fresh.

Known gap: if ralph is running but its state file is mid-write or corrupted,
`is_ralph_active()` returns false and foreman-run may incorrectly start a second
ralph on the same project directory.

### How claude-code handles an existing `ralph-tasks.md`

**Q: Does claude-code rewrite the task file on every iteration?**
No. From iteration 2 onward, ralph injects the current `ralph-tasks.md` contents
into the prompt and tells claude-code which task is current. Claude-code is only
expected to update the status of the current task (`[ ]` → `[/]` → `[x]`), not
rewrite the whole file.

**Q: What if `.ralph/ralph-tasks.md` already exists when ralph starts (e.g. resuming)?**
Ralph uses it as-is from iteration 1. It does not clear or overwrite it on start.
Claude-code sees the existing tasks — including already-completed `[x]` ones — and
continues from where it left off. This is the intended resume mechanism.

**Q: Can a leftover `ralph-tasks.md` from a previous PRD confuse the next one?**
Yes. If `.ralph/ralph-tasks.md` has the old PRD's completed tasks, claude-code on
iteration 1 sees "all tasks complete" and immediately outputs `COMPLETE` without
doing any new work. This is why `foreman-run.py` calls `backup_stale_ralph()` before
every new job — it moves the whole `.ralph/` out of the way first. Running
`foreman-prepare --clean-ralph` manually before queuing a new PRD avoids this too.

### The "⏳ working..." heartbeat

```
⏳ working... elapsed 2:40 · last activity 0:16 ago
```

This is printed by **ralph** (not foreman, not claude-code). It fires every 10 seconds
when claude-code has produced no output in that window. It means:

- **elapsed** — time since this iteration started
- **last activity** — how long ago claude-code last printed anything

It is a silence detector, not an error. Claude-code is still running; it's just thinking
or doing something that produces no output (e.g. a long file read, a large edit).
If "last activity" climbs past several minutes, the agent may be stuck — check with
`ralph --status --tasks` in another terminal.

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

### A. How foreman-prepare.py matches user stories

`foreman-prepare.py` uses this regex to find and renumber user story IDs:

```python
us_pattern = re.compile(r"US-(\d+)", re.IGNORECASE)
```

It matches `US-` followed by **one or more digits**. Examples:

| Text in your PRD | Matches? |
|---|---|
| `## US-1  Upload files` | YES → renumbered to `US-007` |
| `## US-001  Upload files` | YES → renumbered to `US-007` |
| `### US-003  Upload files` | YES → renumbered to `US-007` |
| `## US-x  Upload files` | **NO** — `x` is not a digit |

The script only renumbers stories in **newly renamed files** (files that just got their
`prd-NN-` prefix assigned in this run). Already-numbered PRDs are only scanned to find
the current max ID so new IDs continue from there.

### B. Why `### US-003` stories are not picked up by ralph

Writing `### US-003` is fine for foreman-prepare (it matches the regex above).
The problem is elsewhere in the pipeline.

**Ralph does not parse your PRD.** The entire PRD file is passed verbatim as the
prompt text to claude-code. On iteration 1, ralph's prompt says:

> "TASKS MODE: (no tasks found)
> Create .ralph/ralph-tasks.md with your task list."

Claude-code reads your PRD, understands the user stories, and must write them
as checkboxes in `.ralph/ralph-tasks.md`:

```markdown
- [ ] US-003 implement login page
- [ ] US-004 add logout button
```

Ralph only tracks the `- [ ]` / `- [/]` / `- [x]` format. If `ralph-tasks.md`
is empty or has no checkboxes, ralph reports "no tasks found" and loops without
making progress.

**To debug:**

1. Check that the task file was created:
   ```bash
   cat .ralph/ralph-tasks.md
   ```
   If it doesn't exist or is empty after iteration 1, claude-code failed to plan.

2. Check the stream log for what claude-code actually did:
   ```bash
   # if using foreman-run
   cat done/prd-07-my-feature-stream-<ts>.log
   # if running ralph directly, look in current terminal output
   ```

3. Check if the loop is stuck with stale state:
   ```bash
   ralph --status --tasks
   ```
   If it shows `active: true` but no process is running, clear it:
   ```bash
   mv .ralph done/stale-ralph-manual/
   ```

4. Seed the tasks manually if claude-code refuses to plan:
   ```bash
   ralph --add-task "US-003 implement login page"
   ralph --add-task "US-004 add logout button"
   ```
   Then restart ralph pointing at the PRD. It will pick up the pre-seeded tasks.

5. Run with more iterations to give claude-code time to plan AND start working:
   ```bash
   ralph --file tasks/prd-07-my-feature.md --tasks --agent claude-code --max-iterations 10
   ```
   The default `--max-iterations 3` in foreman-run.py may be too low for complex PRDs.

### C. US placeholder format summary

| Format | Works with foreman-prepare? | Notes |
|---|---|---|
| `US-x` | NO | `x` is not a digit, regex won't match |
| `US-1` | YES | simplest placeholder |
| `US-001` | YES | also fine, gets renumbered anyway |
| `### US-003` | YES (heading level doesn't matter) | already has digits |
| `## US-x` | NO | same issue as first row |

Use `US-1`, `US-2`, ... as your placeholders when writing a new PRD.
