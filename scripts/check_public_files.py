#!/usr/bin/env python3
"""Check tracked files before publication; never prints matched secrets."""
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
result = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True, check=True)
files = [Path(p.decode()) for p in result.stdout.split(b'\0') if p]
blocked_dirs = {'data', 'playlists', 'cache', '.tools', '.local-backups', '.aws', '.venv', '__pycache__'}
patterns = [rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
            rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'github_pat_[A-Za-z0-9_]{40,}',
            rb'AKIA[0-9A-Z]{16}']
issues = []
for file in files:
    if (any(part in blocked_dirs for part in file.parts) or file.name in {'config.json', '.env'}
            or '.backup' in file.name or file.suffix in {'.bak', '.log', '.pem', '.key'}):
        issues.append((file, 'local/generated file is tracked'))
        continue
    path = root / file
    if not path.is_file():
        continue
    if path.stat().st_size > 10 * 1024 * 1024:
        issues.append((file, 'unexpectedly large file'))
        continue
    if any(re.search(pattern, path.read_bytes()) for pattern in patterns):
        issues.append((file, 'possible credential; inspect locally'))
for file, reason in issues:
    print(f'{file}: {reason}')
print(f'Checked {len(files)} tracked files; {len(issues)} issue(s).')
sys.exit(bool(issues))
