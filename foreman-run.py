#!/usr/bin/env python3
"""
foreman-run.py

Monitors todo/ for PRD symlinks matching prd-{number}-{anything}.md.
When a new one appears, runs ralph in --tasks mode, polls completion,
then archives the .ralph/ state and stream log to done/.

Workflow:
    1. User runs: python foreman-prepare.py   (numbers PRDs and user stories in tasks/)
    2. User symlinks: ln -s ../tasks/prd-07-xxx.md todo/prd-07-xxx.md
    3. This script detects the symlink → runs ralph (patched -logfile fork)
    4. Polls: ralph --status --tasks until Progress N/N complete
    5. Archives .ralph/ → done/prd-07-xxx-ralph-YYYYMMDD-HHMMSS/
    6. Moves stream log → done/prd-07-xxx-stream-YYYYMMDD-HHMMSS.log
    7. Removes symlink from todo/

Requires the patched ralph fork (version ending with -logfile).

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
import subprocess
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_POLL_INTERVAL   = 5   # seconds between todo/ scans
DEFAULT_STATUS_INTERVAL = 30  # seconds between ralph --status polls while running
DEFAULT_MAX_ITERATIONS  = 3
DEFAULT_AGENT           = 'claude-code'

# Matches prd-{one_or_more_digits}-{anything}.md
PRD_PATTERN = re.compile(r'^prd-(\d+)-.+\.md$')

TODO_DIR  = Path('todo')
DONE_DIR  = Path('done')
RALPH_DIR = Path('.ralph')
STATE_FILE = Path('.todo_monitor.json')


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
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {'processed': [], 'in_progress': None, 'current_ralph_log': ''}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def scan_todo() -> list:
    """Return PRD paths in todo/ matching the pattern, sorted by prd number."""
    if not TODO_DIR.exists():
        return []
    entries = []
    for p in TODO_DIR.iterdir():
        m = PRD_PATTERN.match(p.name)
        if m and p.exists():   # .exists() follows symlinks, so broken links are skipped
            entries.append((int(m.group(1)), p))
    entries.sort(key=lambda x: x[0])
    return [p for _, p in entries]


def get_ralph_status() -> str:
    """Run ralph --status --tasks and return combined stdout+stderr."""
    try:
        r = subprocess.run(
            ['ralph', '--status', '--tasks'],
            capture_output=True, text=True, timeout=30
        )
        return r.stdout + r.stderr
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
        data = json.loads(state_file.read_text())
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


def start_ralph(prd_path: Path, log_path: Path, max_iterations: int, agent: str,
                gnome_terminal: bool = False, model: str = '',
                no_allow_all: bool = False, extra_ralph_flags: list = [],
                ralph_log_file: str = ''):
    """
    Spawn ralph as a subprocess.

    gnome_terminal=False (default):
        ralph runs silently; stdout/stderr are captured to log_path.
        Use 'ralph --status --tasks' in another terminal for live progress.

    gnome_terminal=True:
        ralph is launched in a new gnome-terminal window so you can watch it live.
        The window title is the PRD filename.
        Output is ALSO tee'd to log_path for archival.
        Because gnome-terminal --wait is used, the returned process tracks the
        terminal window lifetime (not the ralph process directly), so exit-code
        checking is still meaningful (0 = window closed normally).
    """
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
    if ralph_log_file:
        ralph_cmd += ['--log-file', ralph_log_file]
    if extra_ralph_flags:
        ralph_cmd += extra_ralph_flags
    log(f"Starting: {' '.join(ralph_cmd)}")
    log(f"Log: {log_path}")

    if gnome_terminal:
        # Tee ralph output to both the terminal window AND a log file.
        # gnome-terminal --wait: the Popen tracks the terminal window process,
        # which exits when the shell inside it finishes.
        # We use 'bash -c "cmd 2>&1 | tee logfile; echo; read -p DONE"' so the
        # window stays open until the user presses Enter (gives them time to read).
        inner = (
            f"{' '.join(ralph_cmd)} 2>&1 | tee {str(log_path)!r}; "
            f"echo; echo '--- ralph finished --- press Enter to close ---'; read"
        )
        cmd = [
            'gnome-terminal',
            '--wait',
            f'--title=ralph: {prd_path.name}',
            '--',
            'bash', '-c', inner,
        ]
        log("Launching in gnome-terminal window (watch it there for live output)")
        proc = subprocess.Popen(cmd)
        proc._log_fh = None
    else:
        log("Tip: run 'ralph --status --tasks' in another terminal to watch progress")
        lf = open(log_path, 'w')
        proc = subprocess.Popen(ralph_cmd, stdout=lf, stderr=lf)
        proc._log_fh = lf

    return proc


# ---------------------------------------------------------------------------
# Finish / cleanup helpers
# ---------------------------------------------------------------------------

def handle_finished_job(current_prd: Path, state: dict, success: bool,
                        done: int = 0, total: int = 0, ralph_log_file: str = ''):
    """
    Called after ralph exits (or is detected complete via --status).
    On success: archive .ralph/, remove symlink, mark processed.
    On partial: warn, leave .ralph/ and symlink for manual review, still mark processed
                so the monitor doesn't re-queue it on restart.
    In both cases: move the ralph stream log to done/ if it exists.
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

    # Move the ralph stream log to done/ regardless of success/failure
    if ralph_log_file:
        log_p = Path(ralph_log_file)
        if log_p.exists():
            DONE_DIR.mkdir(exist_ok=True)
            dest = DONE_DIR / log_p.name
            shutil.move(str(log_p), str(dest))
            log(f"Moved ralph log → {dest}")
        else:
            log(f"Note: ralph log not found at {log_p} (nothing to move)")

    state['processed'].append(current_prd.name)
    state['in_progress'] = None
    state['current_ralph_log'] = ''
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
            '  done/    Completed archives: .ralph/ state dirs and stream logs\n'
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
            '    7. Moves the stream log    →  done/prd-07-my-feature-stream-<ts>.log\n'
            '    8. Removes the todo/ symlink, then loops back to check for the next PRD\n'
            '\n'
            'Run from the project root (same directory as tasks/, todo/, done/, .ralph/).'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--poll-interval',   type=int, default=DEFAULT_POLL_INTERVAL,
                        help=f'Seconds between todo/ scans (default {DEFAULT_POLL_INTERVAL})')
    parser.add_argument('--status-interval', type=int, default=DEFAULT_STATUS_INTERVAL,
                        help=f'Seconds between ralph --status polls (default {DEFAULT_STATUS_INTERVAL})')
    parser.add_argument('--max-iterations',  type=int, default=DEFAULT_MAX_ITERATIONS,
                        help=f'--max-iterations passed to ralph (default {DEFAULT_MAX_ITERATIONS})')
    parser.add_argument('--agent',           default=DEFAULT_AGENT,
                        help=f'--agent passed to ralph (default {DEFAULT_AGENT})')
    parser.add_argument('--term',            action='store_true', default=False,
                        help='Launch ralph inside a new gnome-terminal window '
                             '(lets you watch progress live; status polling still works)')
    parser.add_argument('--model',           default='',
                        help='Model passed to ralph --model (e.g. claude-sonnet-4-5)')
    parser.add_argument('--allow-all',       action='store_true', default=True,
                        help='(Default) Pass --allow-all to ralph so claude-code '
                             'auto-approves all tool permissions (--dangerously-skip-permissions).')
    parser.add_argument('--no-allow-all',    action='store_true', default=False,
                        help='Pass --no-allow-all to ralph, disabling the default '
                             '--dangerously-skip-permissions bypass (enables interactive prompts). '
                             'By default ralph allows all permissions automatically.')
    parser.add_argument('--ralph-log-file',  default='',
                        help='Override the auto-generated ralph stream log path. '
                             'By default a file named {prd-stem}-stream-{ts}.log is created '
                             'in the current directory and moved to done/ after completion.')
    parser.add_argument('--',                dest='extra_ralph_flags', nargs=argparse.REMAINDER,
                        default=[],
                        help='Extra flags passed verbatim to ralph '
                             '(e.g. -- --verbose-tools)')
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Verify ralph version has -logfile suffix (our patched fork)
    # ------------------------------------------------------------------
    try:
        r = subprocess.run(['ralph', '--version'], capture_output=True, text=True, timeout=10)
        ralph_version = (r.stdout + r.stderr).strip()
        if not ralph_version.endswith('-logfile'):
            log(f"ERROR: ralph version '{ralph_version}' is not the patched fork.")
            log("       Expected a version ending with '-logfile' (open-ralph-wiggum/ralph.ts).")
            log("       Run: cd open-ralph-wiggum && bun install && bun link")
            sys.exit(1)
        log(f"ralph version: {ralph_version} ✓")
    except FileNotFoundError:
        log("ERROR: 'ralph' not found in PATH. Install the patched fork first.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        log("ERROR: 'ralph --version' timed out.")
        sys.exit(1)

    # Ensure directories exist
    TODO_DIR.mkdir(exist_ok=True)
    DONE_DIR.mkdir(exist_ok=True)

    state = load_state()
    current_proc: subprocess.Popen | None = None
    current_prd:  Path | None = None
    last_status_check = 0.0

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------
    def shutdown(signum, frame):
        log("Interrupt received. Shutting down monitor.")
        if current_proc and current_proc.poll() is None:
            log(f"Ralph (pid {current_proc.pid}) is still running — NOT killed.")
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
    current_ralph_log: str = ''
    if state.get('in_progress'):
        prev = Path(state['in_progress'])
        prev_ralph_log = state.get('current_ralph_log', '')
        log(f"Previous session had in-progress job: {prev.name}")
        status = get_ralph_status()

        if is_all_complete(status):
            # Ralph finished while the monitor was down — clean up now
            log("Ralph completed while monitor was offline. Cleaning up.")
            prog = parse_progress(status)
            done_n, total_n = prog if prog else (0, 0)
            handle_finished_job(prev, state, success=True, done=done_n, total=total_n,
                                 ralph_log_file=prev_ralph_log)

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
                                 total=prog[1] if prog else 0,
                                 ralph_log_file=prev_ralph_log)

        else:
            # Ralph appears to still be running in another terminal
            log("Ralph still appears to be running. Will monitor via --status polling.")
            current_prd = prev
            current_ralph_log = prev_ralph_log
            # current_proc stays None — we'll use --status to detect completion

    # ------------------------------------------------------------------
    # Main watch loop
    # ------------------------------------------------------------------
    log(f"Watching {TODO_DIR}/ every {args.poll_interval}s  "
        f"(ralph status check every {args.status_interval}s) ...")
    log("Press Ctrl+C to stop (any active ralph job will keep running).")

    while True:
        now = time.time()

        # ── A. Spawned ralph process finished ──────────────────────────────
        if current_proc is not None and current_proc.poll() is not None:
            if getattr(current_proc, '_log_fh', None) is not None:
                current_proc._log_fh.close()
            exit_code = current_proc.returncode
            log(f"Ralph exited (exit code {exit_code})")

            status = get_ralph_status()
            prog   = parse_progress(status)
            done_n, total_n = prog if prog else (0, 0)
            success = is_all_complete(status)
            handle_finished_job(current_prd, state, success=success,
                                 done=done_n, total=total_n,
                                 ralph_log_file=current_ralph_log)
            current_proc      = None
            current_prd       = None
            current_ralph_log = ''

        # ── B. Monitoring a resumed job (no process handle) ────────────────
        elif current_prd is not None and current_proc is None:
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
                                        total=prog[1] if prog else 0,
                                        ralph_log_file=current_ralph_log)
                    current_prd       = None
                    current_ralph_log = ''
                last_status_check = now

        # ── C. Status heartbeat while ralph is running (for logging) ───────
        elif current_proc is not None and current_proc.poll() is None:
            if (now - last_status_check) >= args.status_interval:
                status = get_ralph_status()
                prog   = parse_progress(status)
                if prog:
                    done_n, total_n = prog
                    log(f"Status: {done_n}/{total_n} tasks complete "
                        f"(ralph pid {current_proc.pid} running)")
                last_status_check = now

        # ── D. Idle: scan todo/ for new PRDs ───────────────────────────────
        if current_proc is None and current_prd is None:
            for prd_path in scan_todo():
                if prd_path.name in state['processed']:
                    continue  # already handled

                # Safety: refuse to start if ralph's state file shows an active loop
                if is_ralph_active():
                    log(f"Skipping {prd_path.name}: ralph state file shows an active loop "
                        f"({RALPH_DIR / 'ralph-loop.state.json'}). Will retry next poll.")
                    break

                # New PRD found — guard against stale .ralph/ first
                backup_stale_ralph()

                ts_str   = datetime.now().strftime('%Y%m%d-%H%M%S')
                log_path = DONE_DIR / f"{prd_path.stem}-ralph-{ts_str}.log"

                # Ralph stream log: named by PRD stem + timestamp, lives in cwd until
                # completion, then moved to done/ by handle_finished_job.
                ralph_log = args.ralph_log_file or f"{prd_path.stem}-stream-{ts_str}.log"

                current_prd       = prd_path
                current_ralph_log = ralph_log
                state['in_progress']       = str(prd_path)
                state['current_ralph_log'] = ralph_log
                save_state(state)

                current_proc      = start_ralph(prd_path, log_path,
                                                args.max_iterations, args.agent,
                                                gnome_terminal=args.term,
                                                model=args.model,
                                                no_allow_all=args.no_allow_all,
                                                extra_ralph_flags=args.extra_ralph_flags,
                                                ralph_log_file=ralph_log)
                last_status_check = now
                break  # one at a time: remaining PRDs will be picked up next iteration

        time.sleep(args.poll_interval)


if __name__ == '__main__':
    main()
