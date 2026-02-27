#!/usr/bin/env python3
"""
foreman-add.py

Interactively creates a new task file in tasks/ and queues it in todo/
for processing by foreman-run.py.

Usage:
    python foreman-add.py [-f plan-feature-xxx.md]

Options:
    -f FILE    Existing plan/feature file to include as reference content
"""

import re
import sys
import argparse
from pathlib import Path

TASKS_DIR = Path('tasks')
TODO_DIR  = Path('todo')


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def prompt_multiline(label: str) -> str:
    """Prompt user for multi-line input; empty line finishes."""
    print(f"{label} (enter a blank line to finish):")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Create a new task file and queue it for foreman-run.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  foreman-add                          # fully interactive\n'
            '  foreman-add -f plan-auth-refactor.md # seed from existing plan file\n'
        ),
    )
    parser.add_argument(
        '-f', '--file',
        metavar='PLAN_FILE',
        help='Existing plan file to include as reference (e.g. plan-feature-xxx.md)',
    )
    parser.add_argument(
        '-p', '--passes',
        type=int, default=1, metavar='N',
        help='Number of claude passes (default 1). '
             'Encodes as todo-slug.pN.md in todo/ so foreman-run picks it up automatically. '
             'Use 2 for implement + verify.',
    )
    args = parser.parse_args()

    if args.passes < 1:
        print("Error: --passes must be at least 1.", file=sys.stderr)
        sys.exit(1)

    # ── Load optional plan file ──────────────────────────────────────────────
    plan_content = ''
    if args.file:
        plan_path = Path(args.file)
        if not plan_path.exists():
            print(f"Error: Plan file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        plan_content = plan_path.read_text(encoding='utf-8')
        print(f"Loaded plan file: {args.file}")

    # ── Interactive prompts ──────────────────────────────────────────────────
    print("\n=== Foreman Task Creator ===")
    try:
        title = input("Job title: ").strip()
    except EOFError:
        title = ''
    if not title:
        print("Error: Title cannot be empty.", file=sys.stderr)
        sys.exit(1)

    requirements = prompt_multiline("Requirements")

    # ── Derive filename ──────────────────────────────────────────────────────
    slug      = slugify(title)
    filename  = f"todo-{slug}.md"
    tasks_path = TASKS_DIR / filename

    # ── Build file content ───────────────────────────────────────────────────
    parts = [f"# {title}\n"]
    if requirements:
        parts.append(f"\n## Requirements\n\n{requirements}\n")
    if plan_content:
        parts.append(f"\n## Plan\n\n{plan_content}\n")
    content = ''.join(parts)

    # ── Write task file ──────────────────────────────────────────────────────
    TASKS_DIR.mkdir(exist_ok=True)
    if tasks_path.exists():
        print(
            f"Error: {tasks_path} already exists. Choose a different title or "
            "rename/delete the existing file.",
            file=sys.stderr,
        )
        sys.exit(1)
    tasks_path.write_text(content, encoding='utf-8')
    print(f"\nCreated:  tasks/{filename}")

    # ── Create symlink in todo/ ──────────────────────────────────────────────
    # Pass count > 1 is encoded in the symlink name as todo-slug.pN.md so that
    # foreman-run can read it without touching the task file itself.
    TODO_DIR.mkdir(exist_ok=True)
    stem      = filename.removesuffix('.md')   # todo-{slug}
    link_name = f"{stem}.p{args.passes}.md" if args.passes > 1 else filename
    link_path = TODO_DIR / link_name
    target    = Path('..') / 'tasks' / filename

    if link_path.exists() or link_path.is_symlink():
        print(f"Note: todo/{link_name} already exists — skipping symlink.")
    else:
        link_path.symlink_to(target)
        print(f"Queued:   todo/{link_name} → {target}")

    print(f"\nTask '{title}' is ready. Run foreman-run to process it.")


if __name__ == '__main__':
    main()
