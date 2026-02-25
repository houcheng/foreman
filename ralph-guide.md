## Simple flow

How to create prd-xxxx.md file from simple specification:

claude
```
/ralph-tui-prd

Please describe the feature you want to build, and I'll ask clarifying questions to create a detailed PRD for ralph-tui execution:
[.... input spec....]

Questions:
[.... answer questions...]

write the md into prd-xxx.md
```

Then run
```
ralph --file tasks/prd-floating-menu.md --tasks --max-iterations 3 --agent claude-code  
```
Can monitor
```
ralph --status --tasks
```

Add task
```
ralph --add-task "After user jump to next or previous chapter, as we had persistant location, user may jump to beginning (if first visit) or middle of chapter. When jump not to offset 0 location, shows a button with up arrow icon for 3 seconds, pressing on it would goes to offset 0 of the current chapter."
```

## Resume

If thr ralph is already stopped, can resumed by telling AI below. Take care that *MUST specify prd filename* otherwise AI may refer US-001 of another prd file!
```
ralph "Check current tasks status from this file .ralph/ralph-tasks.md and for task detail see tasks/prd-xxxx.md" --tasks --max-iterations 3 --agent claude-code 
```

Clear
```
rm -fr .ralph/
```

## Starts form 007 and complains

```
- Besides, some settings needed to add but is still not implement. Read prd-reader-interaction-customization.md and add US-004 and US-005 into our current prd.
- In this prd, the US starts from 007.
- writes md to task.      
```


## Reference

Houcheng command
```bash
ralph "Implement spec.md" --tasks --agent claude-code
ralph "Implement spec.md" --tasks --agent open-code --model minimax/minimax-m2.5
ralph "Implement spec.md" --tasks --agent open-code --model z-ai/glm-4.7
```


# Ralph Wiggum Loop — A Practical Guide

## 1. Commands Reference

### Starting a Job

```bash
# Basic run — loops until the agent outputs COMPLETE
ralph "Build a REST API for todos"

# With task mode — agent splits work into trackable tasks
ralph "Implement all items in specification.md and test each" --tasks

# Limit iterations
ralph "Fix the auth bug" --max-iterations 10

# Custom completion signal
ralph "Add tests" --completion-promise "ALL TESTS PASS"

# Read prompt from a file
ralph --prompt-file ./prompt.md --tasks

# Choose a specific agent and model
ralph "Fix the bug" --agent claude-code --model claude-sonnet-4
```

### Monitoring a Running Loop

```bash
# Show loop status (iteration, elapsed time, struggle indicators)
ralph --status

# Show status + current task list
ralph --status --tasks

# List tasks only (see how the agent split the work)
ralph --list-tasks
```

### Injecting Context & Tasks Mid-Loop

```bash
# Add a hint for the next iteration
ralph --add-context "Focus on the auth module first"

# Add a task the agent missed
ralph --add-task "Add input validation for the /users endpoint"

# Remove a task by index
ralph --remove-task 3

# Clear pending context
ralph --clear-context
```

### Stopping

Press `Ctrl+C` in the terminal. Ralph gracefully kills the agent subprocess and clears loop state.

---

## 2. Core Idea: A Dumb Loop Around a Smart Agent

Ralph itself does almost nothing intelligent. It is a simple loop:

```
┌──────────────────────────────────────┐
│  1. Build a prompt with instructions │
│  2. Spawn an AI agent with it        │
│  3. Wait for agent to finish         │
│  4. Scan output for promise signals  │
│  5. If not done → go to step 1      │
└──────────────────────────────────────┘
```

The agent (Claude Code, OpenCode, Codex, etc.) does all the real work — reading code, writing code, running tests, planning tasks. Ralph just keeps restarting it until it says it's done.

This works because the agent **sees its own previous work** each iteration. It reads the files on disk, understands what's already been done, and picks up where it left off. No memory passing, no context window stitching — just files.

---

## 3. Filesystem as Shared State

Ralph uses **no database, no API, no IPC**. All state lives in plain files under `.ralph/`:

```
.ralph/
├── ralph-loop.state.json   # Loop metadata (iteration, agent, promises, etc.)
├── ralph-tasks.md          # Task list (markdown checkboxes)
├── ralph-context.md        # User-injected hints (consumed once per iteration)
└── ralph-history.json      # History of all iterations (duration, tools, errors)
```

| File | Written by | Read by |
|---|---|---|
| `ralph-loop.state.json` | Ralph | Ralph, `--status` |
| `ralph-tasks.md` | AI agent + user (`--add-task`) | Ralph (to build prompt), user (`--list-tasks`) |
| `ralph-context.md` | User (`--add-context`) | Ralph (injected into next prompt, then deleted) |
| `ralph-history.json` | Ralph | `--status` (struggle detection, timing) |

Because everything is plain files, you can:

- `cat .ralph/ralph-tasks.md` to watch progress
- Edit `ralph-tasks.md` in your editor to reorder or add tasks
- Read `ralph-history.json` to debug slow iterations
- All monitoring commands work from a **second terminal** while the loop runs

---

## 4. Tasks Mode Flow

When you run with `--tasks`, Ralph enters a structured task-tracking workflow. Here's what happens step by step:

### Iteration 1 — The Agent Plans

```
User runs:  ralph "Implement spec.md" --tasks
                │
                ▼
Ralph creates empty .ralph/ralph-tasks.md
                │
                ▼
Prompt to agent:
  "You are in an iterative loop working through a task list.
   TASKS MODE: (no tasks found)
   Your Main Goal: Implement spec.md
   Task Workflow: pick [ ] tasks, mark [/], complete, mark [x]..."
                │
                ▼
Agent reads specification.md
Agent splits it into tasks
Agent writes .ralph/ralph-tasks.md:
  - [ ] Set up project structure
  - [ ] Implement user authentication
  - [ ] Build CRUD endpoints
  - [ ] Add input validation
  - [ ] Write integration tests
                │
                ▼
Agent marks first task [/], works on it, marks [x]
Agent outputs: <promise>READY_FOR_NEXT_TASK</promise>
                │
                ▼
Ralph detects task signal → continues to Iteration 2
```

### Iteration 2..N — The Agent Executes

```
Ralph re-reads .ralph/ralph-tasks.md (now populated)
                │
                ▼
Prompt includes:
  "🔄 CURRENT TASK: Build CRUD endpoints
   📋 Tasks: ✅ project structure, ✅ auth, [/] CRUD, [ ] validation, [ ] tests"
                │
                ▼
Agent picks up the current [/] task (or first [ ] if none in-progress)
Agent implements it, verifies, marks [x]
Agent outputs: <promise>READY_FOR_NEXT_TASK</promise>
                │
                ▼
Ralph continues to next iteration
```

### Final Iteration — All Done

```
Agent sees all tasks are [x]
Agent outputs: <promise>COMPLETE</promise>
                │
                ▼
Ralph detects completion → prints summary → exits
```

### Signal Summary

| Signal | Meaning | Ralph's Action |
|---|---|---|
| `<promise>READY_FOR_NEXT_TASK</promise>` | Current task done | Continue to next iteration |
| `<promise>COMPLETE</promise>` | All tasks done | Exit the loop |
| `<promise>{abort-promise}</promise>` | Precondition failed | Abort with error |
| *(none detected)* | Still working | Continue to next iteration |

### Key Insight

Ralph **never** parses the spec or creates tasks. The AI agent does all the planning. Ralph only reads the task file to tell the agent what's current/next in the prompt. The task file on disk is the single source of truth — both Ralph and the agent read and trust it.
