#!/usr/bin/env python3
"""
foreman-prepare.py

Prepares PRD files in tasks/ for execution by foreman-run.py:
  - Assigns prd-NN- number prefixes to un-numbered PRD files
  - Assigns US-x numbers to user stories within each PRD

Usage:
    python foreman-prepare.py [--dir tasks]
"""
import os
import re
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(
        description='Number PRD files and user stories in tasks/ ready for foreman-run.'
    )
    parser.add_argument("--dir", default="tasks", help="Directory to search for md files (default: tasks)")
    parser.add_argument("--doc", action="store_true", help="Show FOREMAN.md documentation")
    parser.add_argument("--link", action="store_true", help="Create symlinks in todo/ for each processed PRD file")
    args = parser.parse_args()

    if args.doc:
        import sys
        if sys.platform == 'win32':
            doc_path = Path(r'C:\bin\foreman\FOREMAN.md')
        else:
            doc_path = Path(__file__).resolve().parent / 'FOREMAN.md'
        print(doc_path.read_text(encoding='utf-8') if doc_path.exists() else f"FOREMAN.md not found at {doc_path}")
        return

    cwd = Path.cwd() / args.dir
    
    if not cwd.exists():
        print(f"Error: Directory '{args.dir}' does not exist")
        return
    
    md_files = list(cwd.glob("*.md"))
    
    numbered_pattern = re.compile(r"^prd-(\d{2})-.+\.md$")
    numbered_prds = []
    unnumbered_prds = []
    
    for f in md_files:
        match = numbered_pattern.match(f.name)
        if match:
            numbered_prds.append((f, int(match.group(1))))
        elif f.name.startswith("prd-"):
            unnumbered_prds.append(f)
    
    max_prd_num = max((num for _, num in numbered_prds), default=0)
    
    unnumbered_prds.sort(key=lambda f: f.stat().st_mtime)
    
    new_assignments = []
    next_num = max_prd_num + 1
    for f in unnumbered_prds:
        if next_num > 99:
            print(f"Skip {f.name}: would exceed 99")
            continue
        new_name = f"prd-{next_num:02d}-{f.name[4:]}"
        new_path = cwd / new_name
        f.rename(new_path)
        new_assignments.append((new_path, f.name))
        next_num += 1
    
    us_pattern = re.compile(r"US-(\d+)", re.IGNORECASE)
    max_us_num = 0

    for f, _ in numbered_prds:
        content = f.read_text(encoding='utf-8')
        for match in us_pattern.finditer(content):
            num = int(match.group(1))
            if num > max_us_num:
                max_us_num = num
    
    us_counter = max_us_num + 1
    
    for new_path, old_name in new_assignments:
        content = new_path.read_text(encoding='utf-8')
        us_matches = list(us_pattern.finditer(content))
        
        if not us_matches:
            continue
        
        result = []
        last_end = 0
        
        for match in us_matches:
            result.append(content[last_end:match.start()])
            result.append(f"US-{us_counter:03d}")
            last_end = match.end()
            us_counter += 1
        
        result.append(content[last_end:])
        new_path.write_text("".join(result), encoding='utf-8')
    
    print(f"Processed {len(new_assignments)} PRD files")

    if args.link and new_assignments:
        todo_dir = Path.cwd() / "todo"
        todo_dir.mkdir(exist_ok=True)
        for new_path, _ in new_assignments:
            link_path = todo_dir / new_path.name
            target = Path("..") / args.dir / new_path.name
            if link_path.exists() or link_path.is_symlink():
                print(f"Skip symlink (already exists): {link_path}")
            else:
                link_path.symlink_to(target)
                print(f"Linked: todo/{new_path.name} -> {target}")

if __name__ == "__main__":
    main()
