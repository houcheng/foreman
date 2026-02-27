#!/usr/bin/env python3
"""
foreman-status.py

Shows a summary of ralph task history:
  - If .ralph/ralph-tasks.md exists, runs 'ralph --status --tasks' to show active loop status
  - Lists all completed ralph runs in done/ in chronological order with their tasks

Usage:
    python foreman-status.py [--done-dir done]
"""
import re
import subprocess
import argparse
from datetime import datetime
from pathlib import Path


RALPH_DIR  = Path('.ralph')
TASKS_FILE = RALPH_DIR / 'ralph-tasks.md'


def show_active_status():
    if not TASKS_FILE.exists():
        return
    cmd = 'ralph --status --tasks'
    print(f"$ {cmd}")
    subprocess.run(cmd.split())
    print()


def show_done(done_dir: Path):
    if not done_dir.exists():
        print(f"No done/ directory found at '{done_dir}'")
        return

    ts_pattern = re.compile(r'(\d{8}-\d{6})$')
    task_line  = re.compile(r'^\s*-\s*\[(.)\]\s*(.*)')

    entries = []
    for d in done_dir.iterdir():
        if not d.is_dir():
            continue
        tasks_file = d / 'ralph-tasks.md'
        if not tasks_file.exists():
            continue
        m = ts_pattern.search(d.name)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), '%Y%m%d-%H%M%S')
        except ValueError:
            continue
        entries.append((ts, d, tasks_file))

    if not entries:
        print("No completed ralph runs found in done/")
        return

    entries.sort(key=lambda x: x[0])

    for ts, d, tasks_file in entries:
        print(f"\n{'='*60}")
        print(f"  {d.name}")
        print(f"  {ts.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        content = tasks_file.read_text(encoding='utf-8')
        for line in content.splitlines():
            m = task_line.match(line)
            if m:
                mark, text = m.group(1), m.group(2)
                if mark == 'x':
                    status = '[x]'
                elif mark == '/':
                    status = '[/]'
                else:
                    status = '[ ]'
                print(f"  {status} {text}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Report ralph task history: active loop status and completed runs.'
    )
    parser.add_argument('--done-dir', default='done',
                        help='Directory of completed ralph archives (default: done)')
    args = parser.parse_args()

    show_done(Path(args.done_dir))
    show_active_status()


if __name__ == '__main__':
    main()
