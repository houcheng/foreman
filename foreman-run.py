#!/usr/bin/env python3
"""
foreman-run.py

Monitors todo/ for task files and runs them automatically.

Supported task types
--------------------
prd-{number}-{anything}.md   → runs via ralph (--tasks mode)
todo-{anything}.md           → runs via claude (two-pass: implement + verify)
plan-{anything}.md           → runs via claude (two-pass: implement + verify)

Workflow (PRD):
    1. User runs: python foreman-prepare.py   (numbers PRDs and user stories in tasks/)
    2. User symlinks: ln -s ../tasks/prd-07-xxx.md todo/prd-07-xxx.md
    3. This script detects the symlink → runs ralph
    4. Polls: ralph --status --tasks until Progress N/N complete
    5. Archives .ralph/ → done/prd-07-xxx-ralph-YYYYMMDD-HHMMSS/
    6. Removes symlink from todo/

Workflow (todo/plan):
    1. User runs: foreman-add  (creates tasks/todo-xxx.md and symlinks todo/)
       or manually places a plan-xxx.md symlink in todo/
    2. This script detects the symlink → runs two claude passes:
       Pass 1 — "read the FILE and implement it. Output <prompt>COMPLETE</prompt> when done."
       Pass 2 — "read FILE listing what we implemented. Check if done, output <prompt>COMPLETE</prompt>."
    3. Archives log → done/todo-xxx-claude-YYYYMMDD-HHMMSS/
    4. Removes symlink from todo/

Run from project root (same dir as .ralph/, tasks/, todo/, done/).

Usage:
    python foreman-run.py [--poll-interval N] [--status-interval N] [--max-iterations N]
"""

import re
import sys
import json
import time
import shutil
import signal
import argparse
import threading
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_POLL_INTERVAL   = 5   # seconds between todo/ scans
DEFAULT_STATUS_INTERVAL = 30  # seconds between ralph --status polls while running
DEFAULT_MAX_ITERATIONS  = 5
DEFAULT_AGENT           = 'claude-code'

# Matches prd-{one_or_more_digits}-{anything}.md
PRD_PATTERN = re.compile(r'^prd-(\d+)-.+\.md$')

# Matches todo-{anything}.md and plan-{anything}.md (direct-claude tasks)
TODO_TASK_PATTERN = re.compile(r'^(todo|plan)-.+\.md$')

# Signal that claude must output when the task is complete
COMPLETE_SIGNAL = '<prompt>COMPLETE</prompt>'

TODO_DIR  = Path('todo')
DONE_DIR  = Path('done')
TASKS_DIR = Path('tasks')
RALPH_DIR = Path('.ralph')
STATE_FILE = Path('.todo_monitor.json')

# Matches: "You've hit your limit · resets 1pm (Asia/Taipei)"
RATE_LIMIT_RE = re.compile(
    r"hit your limit[^\n]*resets\s+(\d{1,2}(?::\d{2})?(?:am|pm))\s+\(([^)]+)\)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now().strftime('%H:%M:%S')


def log(msg: str):
    print(f"[{ts()}] {msg}", flush=True)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'in_progress': None}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding='utf-8')


def extract_passes(name: str, default: int = 1) -> int:
    """Extract pass count encoded in a symlink name.

    todo-slug.p2.md  → 2
    todo-slug.md     → default (1)
    """
    m = re.search(r'\.p(\d+)\.md$', name)
    return int(m.group(1)) if m else default


def scan_todo() -> list:
    """Return queued task paths in todo/ as (path, job_type) tuples.

    PRDs (job_type='prd') come first, sorted by number.
    Todo/plan files (job_type='todo') follow, sorted by modification time.
    Broken symlinks are skipped.
    """
    if not TODO_DIR.exists():
        return []
    prds  = []
    todos = []
    for p in TODO_DIR.iterdir():
        if not p.exists():   # .exists() follows symlinks; broken links → False
            continue
        m = PRD_PATTERN.match(p.name)
        if m:
            prds.append((int(m.group(1)), p))
            continue
        if TODO_TASK_PATTERN.match(p.name):
            todos.append((p.stat().st_mtime, p))
    prds.sort(key=lambda x: x[0])
    todos.sort(key=lambda x: x[0])
    return [(p, 'prd') for _, p in prds] + [(p, 'todo') for _, p in todos]


def get_ralph_status() -> str:
    """Run ralph --status --tasks and return combined stdout+stderr."""
    try:
        r = subprocess.run(
            ['ralph', '--status', '--tasks'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30
        )
        return (r.stdout or '') + (r.stderr or '')
    except subprocess.TimeoutExpired:
        log("Warning: ralph --status timed out")
        return ''
    except Exception as e:
        log(f"Warning: could not run ralph --status: {e}")
        return ''


def parse_progress(status: str):
    """
    Parse completion info from ralph --status --tasks output.
    Primary:  'Progress: N/M complete'
    Fallback: count ✅ vs total numbered task lines

    Returns (completed, total) or None.
    """
    m = re.search(r'Progress:\s*(\d+)/(\d+)\s*complete', status)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Fallback: scan task lines like '   1. ✅ US-xxx ...'
    task_lines = re.findall(r'^\s+\d+\.\s+(\S)', status, re.MULTILINE)
    if task_lines:
        total = len(task_lines)
        # ✅ is the unicode checkmark ralph uses for complete tasks
        done  = sum(1 for c in task_lines if c == '✅')
        return done, total

    return None


def is_no_active_loop(status: str) -> bool:
    return 'No active loop' in status


def is_all_complete(status: str) -> bool:
    prog = parse_progress(status)
    if prog:
        done, total = prog
        return total > 0 and done == total
    return False


def archive_ralph_state(prd_name: str) -> Path | None:
    """Move .ralph/ → done/{prd_stem}-ralph-{timestamp}/"""
    if not RALPH_DIR.exists():
        log("Warning: .ralph/ not found — nothing to archive")
        return None
    DONE_DIR.mkdir(exist_ok=True)
    stem    = prd_name.removesuffix('.md') if prd_name.endswith('.md') else prd_name
    ts_str  = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest    = DONE_DIR / f'{stem}-ralph-{ts_str}'
    shutil.move(str(RALPH_DIR), str(dest))
    log(f"Archived .ralph/ → {dest}/")
    return dest


def archive_todo_result(task_path: Path, success: bool, log_file: Path | None) -> Path:
    """Archive todo/plan task results to done/{stem}-claude-{timestamp}/"""
    DONE_DIR.mkdir(exist_ok=True)
    stem   = task_path.name.removesuffix('.md')
    ts_str = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest   = DONE_DIR / f'{stem}-claude-{ts_str}'
    dest.mkdir(exist_ok=True)

    status_text = 'COMPLETE' if success else 'INCOMPLETE'
    status_file = dest / 'status.md'
    status_file.write_text(
        f"# Task Status\n\n"
        f"**Task:** {task_path.name}\n"
        f"**Status:** {status_text}\n"
        f"**Completed:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        encoding='utf-8',
    )

    if log_file and log_file.exists():
        shutil.move(str(log_file), str(dest / log_file.name))

    log(f"Archived todo task → {dest}/")
    return dest


def is_ralph_active() -> bool:
    """Return True if ralph's state file reports an active loop.

    Reads .ralph/ralph-loop.state.json and checks the 'active' flag.
    This is more reliable than pgrep because it uses ralph's own state
    rather than scanning the process table.
    """
    state_file = RALPH_DIR / 'ralph-loop.state.json'
    if not state_file.exists():
        return False
    try:
        data = json.loads(state_file.read_text(encoding='utf-8'))
        return bool(data.get('active', False))
    except Exception:
        return False


def backup_stale_ralph():
    """Back up an unexpected .ralph/ before starting a new job."""
    if RALPH_DIR.exists():
        DONE_DIR.mkdir(exist_ok=True)
        ts_str = datetime.now().strftime('%Y%m%d-%H%M%S')
        dest   = DONE_DIR / f'stale-ralph-{ts_str}'
        shutil.move(str(RALPH_DIR), str(dest))
        log(f"Backed up stale .ralph/ → {dest}/ (unexpected leftover)")


def remove_symlink(p: Path):
    try:
        if p.is_symlink():
            p.unlink()
            log(f"Removed symlink: todo/{p.name}")
        else:
            log(f"Note: todo/{p.name} is a regular file — leaving it in place")
    except Exception as e:
        log(f"Warning: could not remove {p}: {e}")


def parse_reset_datetime(time_str: str, tz_name: str):
    """Parse '1pm' / '1:30pm' in a named timezone into a future datetime."""
    try:
        tz = ZoneInfo(tz_name)
        time_str = time_str.strip().upper()
        fmt = '%I:%M%p' if ':' in time_str else '%I%p'
        t = datetime.strptime(time_str, fmt).time()
        now_local = datetime.now(tz)
        reset = now_local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if reset <= now_local:
            reset += timedelta(days=1)
        return reset
    except Exception:
        return None


def _pipe_output(proc, rate_limit_info: dict):
    """Echo ralph stdout to terminal; detect rate-limit message."""
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if not rate_limit_info['detected']:
                m = RATE_LIMIT_RE.search(line)
                if m:
                    reset_dt = parse_reset_datetime(m.group(1), m.group(2))
                    rate_limit_info['detected'] = True
                    rate_limit_info['reset_time'] = reset_dt
                    ts_str = reset_dt.strftime('%H:%M %Z') if reset_dt else 'unknown'
                    log(f"Rate limit detected. Resets at {ts_str}")
    except Exception:
        pass


def _pipe_claude_output(proc, output_info: dict, log_file: Path):
    """Echo claude stdout to terminal and log file; detect COMPLETE signal."""
    try:
        with open(log_file, 'a', encoding='utf-8', errors='replace') as f:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                f.write(line)
                if COMPLETE_SIGNAL in line:
                    output_info['complete'] = True
    except Exception:
        pass


def start_ralph(prd_path: Path, max_iterations: int, agent: str,
                model: str = '', no_allow_all: bool = False,
                extra_ralph_flags: list = []):
    """Spawn ralph as a subprocess, piping output through foreman for rate-limit detection."""
    ralph_cmd = [
        'ralph',
        '--file', str(prd_path),
        '--tasks',
        '--max-iterations', str(max_iterations),
        '--agent', agent,
    ]
    if model:
        ralph_cmd += ['--model', model]
    if no_allow_all:
        ralph_cmd += ['--no-allow-all']
    if extra_ralph_flags:
        ralph_cmd += extra_ralph_flags
    log(f"Starting: {' '.join(ralph_cmd)}")

    rate_limit_info: dict = {'detected': False, 'reset_time': None}
    proc = subprocess.Popen(ralph_cmd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', bufsize=1)
    t = threading.Thread(target=_pipe_output, args=(proc, rate_limit_info), daemon=True)
    t.start()
    return proc, rate_limit_info


def start_claude_pass(task_path: Path, pass_num: int, log_file: Path,
                      allow_all: bool = True, verify: bool = False,
                      total_passes: int = 1) -> tuple:
    """Spawn claude for an implementation or verification pass.

    verify=False (default): implement prompt — "read FILE and implement it."
    verify=True:            check prompt    — "read FILE and verify all is done."
    """
    if verify:
        prompt = (
            f"read {task_path} that lists the functions/features we've implemented. "
            f"Check whether everything described is done and output `{COMPLETE_SIGNAL}`."
        )
        pass_label = 'verify'
    else:
        prompt = (
            f"read the {task_path} and implement it. "
            f"When it is all done output `{COMPLETE_SIGNAL}`."
        )
        pass_label = 'implement'

    cmd = ['claude', '--print']
    if allow_all:
        cmd += ['--dangerously-skip-permissions']
    cmd += [prompt]

    # Write a header section to the log before starting
    with open(log_file, 'a', encoding='utf-8', errors='replace') as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Pass {pass_num}/{total_passes} ({pass_label}) — "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Prompt: {prompt}\n")
        f.write(f"{'='*60}\n\n")

    log(f"Starting claude pass {pass_num}/{total_passes} [{pass_label}] for {task_path.name}")

    output_info: dict = {'complete': False}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', bufsize=1,
    )
    t = threading.Thread(
        target=_pipe_claude_output, args=(proc, output_info, log_file), daemon=True
    )
    t.start()
    return proc, output_info


# ---------------------------------------------------------------------------
# Finish / cleanup helpers
# ---------------------------------------------------------------------------

def handle_finished_job(current_prd: Path, state: dict, success: bool,
                        done: int = 0, total: int = 0):
    """
    Called after ralph exits (or is detected complete via --status).
    On success: archive .ralph/, remove symlink.
    On partial: warn, leave .ralph/ and symlink in place for manual review.
    """
    if success:
        log(f"All {total}/{total} tasks complete for {current_prd.name}!")
        archive_ralph_state(current_prd.name)
        remove_symlink(current_prd)
    else:
        if total > 0:
            log(f"WARNING: ralph finished but only {done}/{total} tasks done.")
        else:
            log("WARNING: ralph finished but task progress could not be determined.")
        log("Leaving .ralph/ and todo symlink in place for manual review.")
        log(f"To resume: ralph --file {current_prd} --tasks --agent {DEFAULT_AGENT}")

    state['in_progress'] = None
    save_state(state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            'Monitor todo/ for PRD files and run ralph automatically.\n'
            '\n'
            'Folders:\n'
            '  tasks/   PRD source files (authored here, numbered by prd_prepare.py)\n'
            '  todo/    Symlinks to PRDs queued for execution (one at a time)\n'
            '  done/    Completed archives: .ralph/ state dirs\n'
            '  .ralph/  Active ralph loop state (created by ralph, archived on completion)\n'
            '\n'
            'PRD workflow:\n'
            '\n'
            '  You do (once per PRD):\n'
            '    1. Write a PRD            tasks/prd-my-feature.md\n'
            '    2. Assign numbers          python prd_prepare.py\n'
            '         Renames to tasks/prd-07-my-feature.md and assigns US-x numbers\n'
            '         to each user story inside the file.\n'
            '    3. Queue it               ln -s ../tasks/prd-07-my-feature.md todo/prd-07-my-feature.md\n'
            '\n'
            '  This script does the rest automatically:\n'
            '    4. Detects the new symlink in todo/ and launches:\n'
            '         ralph --file todo/prd-07-my-feature.md --tasks --agent claude-code ...\n'
            '    5. Polls "ralph --status --tasks" every N seconds until all tasks complete\n'
            '    6. Archives .ralph/ state  →  done/prd-07-my-feature-ralph-<ts>/\n'
            '    7. Removes the todo/ symlink, then loops back to check for the next PRD\n'
            '\n'
            'Todo/plan workflow:\n'
            '\n'
            '  You do (once per task):\n'
            '    1. foreman-add            (creates tasks/todo-xxx.md and todo/ symlink)\n'
            '       or: ln -s ../tasks/plan-xxx.md todo/plan-xxx.md\n'
            '\n'
            '  This script does the rest automatically:\n'
            '    2. Detects todo-*.md / plan-*.md in todo/\n'
            '    3. Pass 1: claude --print "read FILE and implement it ..."\n'
            '    4. Pass 2: claude --print "read FILE and verify completion ..."\n'
            '    5. Archives log → done/{name}-claude-<ts>/\n'
            '    6. Removes the todo/ symlink\n'
            '\n'
            'Run from the project root (same directory as tasks/, todo/, done/, .ralph/).'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--doc', action='store_true', help='Show FOREMAN.md documentation')
    parser.add_argument('--poll-interval',   type=int, default=DEFAULT_POLL_INTERVAL,
                        help=f'Seconds between todo/ scans (default {DEFAULT_POLL_INTERVAL})')
    parser.add_argument('--status-interval', type=int, default=DEFAULT_STATUS_INTERVAL,
                        help=f'Seconds between ralph --status polls (default {DEFAULT_STATUS_INTERVAL})')
    parser.add_argument('--max-iterations',  type=int, default=DEFAULT_MAX_ITERATIONS,
                        help=f'--max-iterations passed to ralph (default {DEFAULT_MAX_ITERATIONS})')
    parser.add_argument('--max-retries',     type=int, default=3,
                        help='Max times to restart ralph when tasks incomplete after exit code 0 '
                             '(default 10, 0=unlimited)')
    parser.add_argument('--max-error-retries', type=int, default=3,
                        help='Max times to restart ralph after a non-zero exit code '
                             '(default 3, 0=no retry)')
    parser.add_argument('--agent',           default=DEFAULT_AGENT,
                        help=f'--agent passed to ralph (default {DEFAULT_AGENT})')
    parser.add_argument('--model',           default='',
                        help='Model passed to ralph --model (e.g. claude-sonnet-4-5)')
    parser.add_argument('--allow-all',       action='store_true', default=True,
                        help='(Default) Pass --allow-all to ralph so claude-code '
                             'auto-approves all tool permissions (--dangerously-skip-permissions).')
    parser.add_argument('--no-allow-all',    action='store_true', default=False,
                        help='Pass --no-allow-all to ralph, disabling the default '
                             '--dangerously-skip-permissions bypass (enables interactive prompts). '
                             'By default ralph allows all permissions automatically.')
    parser.add_argument('--create-dirs',     action='store_true', default=False,
                        help='Create missing required folders (todo/, done/) and exit.')
    parser.add_argument('--',                dest='extra_ralph_flags', nargs=argparse.REMAINDER,
                        default=[],
                        help='Extra flags passed verbatim to ralph '
                             '(e.g. -- --verbose-tools)')
    args = parser.parse_args()

    if args.doc:
        if sys.platform == 'win32':
            doc_path = Path(r'C:\bin\foreman\FOREMAN.md')
        else:
            doc_path = Path(__file__).resolve().parent / 'FOREMAN.md'
        print(doc_path.read_text(encoding='utf-8') if doc_path.exists() else f'FOREMAN.md not found at {doc_path}')
        return

    # ------------------------------------------------------------------
    # Verify ralph is available
    # ------------------------------------------------------------------
    try:
        r = subprocess.run(['ralph', '--version'], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=10)
        ralph_version = ((r.stdout or '') + (r.stderr or '')).strip()
        log(f"ralph version: {ralph_version} ✓")
    except FileNotFoundError:
        log("ERROR: 'ralph' not found in PATH.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        log("ERROR: 'ralph --version' timed out.")
        sys.exit(1)

    # --create-dirs: create missing folders and exit
    if args.create_dirs:
        for d in (TASKS_DIR, TODO_DIR, DONE_DIR):
            d.mkdir(exist_ok=True)
            print(f"Created: {d}/")
        print("Done. Run foreman-run.py again to start monitoring.")
        return

    # Check required directories exist — do not auto-create them
    missing = [d for d in (TASKS_DIR, TODO_DIR, DONE_DIR) if not d.exists()]
    if missing:
        print("ERROR: The following required folders are missing:")
        for d in missing:
            print(f"  {d}/")
        print()
        print("Run with --doc to see how it works, or --create-dirs to create them.")
        sys.exit(1)

    state = load_state()
    current_proc: subprocess.Popen | None = None
    current_prd:  Path | None = None
    current_rate_limit_info: dict = {}
    last_status_check = 0.0

    # State for todo/plan (direct-claude) jobs
    current_job_type:    str | None  = None   # 'prd' or 'todo'
    current_pass:        int         = 0      # which pass we're on (1-based)
    current_num_passes:  int         = 1      # total passes for this task (from symlink name)
    current_task_log:    Path | None = None   # log file for todo jobs
    current_task_output: dict        = {}     # output_info from start_claude_pass

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------
    def shutdown(signum, frame):
        log("Interrupt received. Shutting down monitor.")
        if current_proc and current_proc.poll() is None:
            agent_name = 'Ralph' if current_job_type == 'prd' else 'Claude'
            log(f"{agent_name} (pid {current_proc.pid}) is still running — NOT killed.")
            log("Re-run this monitor to resume tracking it, or check manually.")
            if current_prd:
                state['in_progress'] = str(current_prd)
        save_state(state)
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ------------------------------------------------------------------
    # On restart: check if a previous session left an in-progress job
    # ------------------------------------------------------------------
    if state.get('in_progress'):
        prev     = Path(state['in_progress'])
        job_type = state.get('job_type', 'prd')
        log(f"Previous session had in-progress job: {prev.name} (type: {job_type})")

        if job_type == 'todo':
            # Cannot resume a todo/plan job mid-pass; restart from pass 1 via symlink
            log("Cannot resume todo job mid-pass. Will restart it from pass 1.")
            state['in_progress'] = None
            save_state(state)
            # The symlink should still exist in todo/ so the main loop will pick it up

        else:
            # PRD job: check ralph status
            status = get_ralph_status()

            if is_all_complete(status):
                # Ralph finished while the monitor was down — clean up now
                log("Ralph completed while monitor was offline. Cleaning up.")
                prog = parse_progress(status)
                done_n, total_n = prog if prog else (0, 0)
                handle_finished_job(prev, state, success=True, done=done_n, total=total_n)

            elif is_no_active_loop(status):
                # Ralph stopped without completing
                prog = parse_progress(status)
                if prog:
                    d, t = prog
                    log(f"Ralph stopped at {d}/{t} tasks. Not all done.")
                else:
                    log("Ralph stopped; could not determine progress.")
                handle_finished_job(prev, state, success=False,
                                     done=prog[0] if prog else 0,
                                     total=prog[1] if prog else 0)

            else:
                # Ralph appears to still be running in another terminal
                log("Ralph still appears to be running. Will monitor via --status polling.")
                current_prd      = prev
                current_job_type = 'prd'
                # current_proc stays None — we'll use --status to detect completion

    # ------------------------------------------------------------------
    # Main watch loop
    # ------------------------------------------------------------------
    log(f"Watching {TODO_DIR}/ every {args.poll_interval}s  "
        f"(ralph status check every {args.status_interval}s) ...")
    log("Press Ctrl+C to stop (any active job will keep running).")

    while True:
        now = time.time()

        # ── A. Spawned process finished ────────────────────────────────────
        if current_proc is not None and current_proc.poll() is not None:
            exit_code = current_proc.returncode

            # ── A-todo: direct-claude job ──────────────────────────────────
            if current_job_type == 'todo':
                complete = current_task_output.get('complete', False)
                log(f"Claude pass {current_pass}/{current_num_passes} exited "
                    f"(exit code {exit_code}), COMPLETE={'yes' if complete else 'no'}")

                if current_pass < current_num_passes:
                    # More passes to run
                    if not complete:
                        log(f"WARNING: Pass {current_pass} did not output COMPLETE signal.")
                    current_pass += 1
                    is_verify  = (current_pass == current_num_passes and current_num_passes > 1)
                    pass_label = 'verify' if is_verify else 'implement'
                    log(f"Starting pass {current_pass}/{current_num_passes} [{pass_label}]...")
                    current_proc, current_task_output = start_claude_pass(
                        current_prd, current_pass, current_task_log,
                        allow_all=not args.no_allow_all,
                        verify=is_verify,
                        total_passes=current_num_passes,
                    )

                else:  # all passes done
                    success = complete
                    if current_num_passes > 1:
                        # last pass was a verify pass
                        if success:
                            log(f"Verification complete for {current_prd.name}!")
                        else:
                            log(f"WARNING: Verification pass did not confirm completion "
                                f"for {current_prd.name}.")
                    else:
                        if success:
                            log(f"Task complete for {current_prd.name}!")
                        else:
                            log(f"WARNING: Task pass did not output COMPLETE "
                                f"for {current_prd.name}.")
                    archive_todo_result(current_prd, success, current_task_log)
                    remove_symlink(current_prd)
                    state['in_progress'] = None
                    save_state(state)
                    current_proc        = None
                    current_prd         = None
                    current_job_type    = None
                    current_pass        = 0
                    current_num_passes  = 1
                    current_task_log    = None
                    current_task_output = {}

            # ── A-prd: ralph job ───────────────────────────────────────────
            else:
                log(f"Ralph exited (exit code {exit_code})")

                status  = get_ralph_status()
                prog    = parse_progress(status)
                done_n, total_n = prog if prog else (0, 0)
                success = is_all_complete(status)

                # Rate limit: wait until reset, then resume (doesn't count as retry)
                if current_rate_limit_info.get('detected'):
                    reset_dt = current_rate_limit_info.get('reset_time')
                    if reset_dt:
                        wait_secs = max(
                            (reset_dt - datetime.now(reset_dt.tzinfo)).total_seconds() + 60, 0
                        )
                        log(f"Waiting {int(wait_secs // 60)}m {int(wait_secs % 60)}s "
                            f"for rate limit reset...")
                        time.sleep(wait_secs)
                    else:
                        log("Rate limit hit (unknown reset time). Waiting 60 minutes...")
                        time.sleep(3600)
                    log("Resuming after rate limit reset.")
                    current_proc, current_rate_limit_info = start_ralph(
                        current_prd, args.max_iterations, args.agent,
                        model=args.model,
                        no_allow_all=args.no_allow_all,
                        extra_ralph_flags=args.extra_ralph_flags)
                    last_status_check = now

                elif not success and exit_code == 0:
                    retry_count = state.get('retry_count', 0) + 1
                    max_retries = args.max_retries
                    if max_retries == 0 or retry_count <= max_retries:
                        state['retry_count'] = retry_count
                        save_state(state)
                        retry_label = (f"{retry_count}/{max_retries}"
                                       if max_retries else f"{retry_count}/∞")
                        log(f"Tasks incomplete ({done_n}/{total_n}). "
                            f"Auto-retrying [{retry_label}]...")
                        current_proc, current_rate_limit_info = start_ralph(
                            current_prd, args.max_iterations, args.agent,
                            model=args.model,
                            no_allow_all=args.no_allow_all,
                            extra_ralph_flags=args.extra_ralph_flags)
                        last_status_check = now
                    else:
                        log(f"Max retries ({max_retries}) reached. "
                            f"Giving up on {current_prd.name}.")
                        handle_finished_job(current_prd, state, success=False,
                                            done=done_n, total=total_n)
                        current_proc = None
                        current_prd  = None
                elif exit_code != 0:
                    error_retry_count = state.get('error_retry_count', 0) + 1
                    max_err = args.max_error_retries
                    if max_err > 0 and error_retry_count <= max_err:
                        state['error_retry_count'] = error_retry_count
                        save_state(state)
                        log(f"Ralph error (exit {exit_code}). "
                            f"Retrying [{error_retry_count}/{max_err}]...")
                        current_proc, current_rate_limit_info = start_ralph(
                            current_prd, args.max_iterations, args.agent,
                            model=args.model,
                            no_allow_all=args.no_allow_all,
                            extra_ralph_flags=args.extra_ralph_flags)
                        last_status_check = now
                    else:
                        log(f"Ralph error (exit {exit_code}), "
                            f"max error retries ({max_err}) reached.")
                        handle_finished_job(current_prd, state, success=False,
                                            done=done_n, total=total_n)
                        current_proc = None
                        current_prd  = None
                else:
                    handle_finished_job(current_prd, state, success=True,
                                         done=done_n, total=total_n)
                    current_proc = None
                    current_prd  = None

        # ── B. Monitoring a resumed PRD job (no process handle) ────────────
        elif current_prd is not None and current_proc is None and current_job_type == 'prd':
            if (now - last_status_check) >= args.status_interval:
                status = get_ralph_status()
                prog   = parse_progress(status)
                if prog:
                    done_n, total_n = prog
                    log(f"Status: {done_n}/{total_n} tasks complete")

                if is_no_active_loop(status):
                    success = is_all_complete(status)
                    handle_finished_job(current_prd, state, success=success,
                                        done=prog[0] if prog else 0,
                                        total=prog[1] if prog else 0)
                    current_prd      = None
                    current_job_type = None
                last_status_check = now

        # ── C. Status heartbeat while a process is running ─────────────────
        elif current_proc is not None and current_proc.poll() is None:
            if (now - last_status_check) >= args.status_interval:
                if current_job_type == 'todo':
                    log(f"Claude pass {current_pass}/{current_num_passes} still running "
                        f"(pid {current_proc.pid})...")
                else:
                    status = get_ralph_status()
                    prog   = parse_progress(status)
                    if prog:
                        done_n, total_n = prog
                        log(f"Status: {done_n}/{total_n} tasks complete "
                            f"(ralph pid {current_proc.pid} running)")
                last_status_check = now

        # ── D. Idle: scan todo/ for new tasks ──────────────────────────────
        if current_proc is None and current_prd is None:
            for task_path, task_type in scan_todo():

                if task_type == 'prd':
                    # Safety: refuse to start if ralph's state file shows an active loop
                    if is_ralph_active():
                        log(f"Skipping {task_path.name}: ralph state file shows an active loop "
                            f"({RALPH_DIR / 'ralph-loop.state.json'}). Will retry next poll.")
                        break

                    # New PRD found — guard against stale .ralph/ first
                    backup_stale_ralph()

                    current_prd      = task_path
                    current_job_type = 'prd'
                    state['in_progress']      = str(task_path)
                    state['job_type']         = 'prd'
                    state['retry_count']      = 0
                    state['error_retry_count'] = 0
                    save_state(state)

                    current_proc, current_rate_limit_info = start_ralph(
                        task_path, args.max_iterations, args.agent,
                        model=args.model,
                        no_allow_all=args.no_allow_all,
                        extra_ralph_flags=args.extra_ralph_flags)
                    last_status_check = now

                else:  # 'todo' — direct-claude job
                    stem             = task_path.name.removesuffix('.md')
                    ts_str           = datetime.now().strftime('%Y%m%d-%H%M%S')
                    DONE_DIR.mkdir(exist_ok=True)
                    current_task_log = DONE_DIR / f'{stem}-{ts_str}.log'

                    current_prd        = task_path
                    current_job_type   = 'todo'
                    current_pass       = 1
                    current_num_passes = extract_passes(task_path.name)
                    state['in_progress'] = str(task_path)
                    state['job_type']    = 'todo'
                    save_state(state)

                    current_proc, current_task_output = start_claude_pass(
                        task_path, 1, current_task_log,
                        allow_all=not args.no_allow_all,
                        verify=False,
                        total_passes=current_num_passes,
                    )
                    last_status_check = now

                break  # one at a time: remaining tasks picked up next iteration

        time.sleep(args.poll_interval)


if __name__ == '__main__':
    main()
