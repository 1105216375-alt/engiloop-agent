"""Allowlist and pattern audit. Prints locations/categories, never matched secrets.

This catches a bounded set of patterns, not all secrets, private data or license
problems. --manifest mechanically records hashes of allowlisted public files.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {'engineering_agent', 'bip_runtime', 'domain', 'nskernel',
               'legacy_v7', 'examples', 'tests', 'docs', 'tools', '.github'}
ROOT_FILES = {'README.md', 'LICENSE', 'NOTICE', 'SECURITY.md', 'CONTRIBUTING.md',
              'MANIFEST.in', 'pyproject.toml', '.gitignore'}
IGNORED = {'.git', '.venv', '__pycache__', '.pytest_cache', 'build', 'dist',
           'outputs', 'runtime'}
EXTENSIONS = {'.py', '.md', '.json', '.yml', '.yaml', '.toml', '.txt'}
PATTERNS = {
    'provider-token': re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential-assignment': re.compile(r'''(?i)(?:api_key|auth_token|password)\s*[:=]\s*['"][A-Za-z0-9/+_=.-]{16,}['"]'''),
    'private-home-path': re.compile(r'(?:/Users/|/home/)[A-Za-z0-9_.-]+/'),
    'credential-url': re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
}


def public_files(root=ROOT):
    files, errors = [], []
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if any(part in IGNORED or part.endswith('.egg-info') for part in rel.parts):
            continue
        if path.name == '.DS_Store':
            continue
        if path.is_symlink():
            errors.append((str(rel), 0, 'symlink-not-allowed'))
            continue
        if not path.is_file():
            continue
        if str(rel) not in ROOT_FILES and not (
            rel.parts[0] in DIRECTORIES and path.suffix in EXTENSIONS
        ):
            errors.append((str(rel), 0, 'file-not-allowlisted'))
            continue
        files.append(path)
    return files, errors


def audit(root=ROOT):
    files, errors = public_files(root)
    for path in files:
        rel = str(path.relative_to(root))
        try:
            content = path.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            errors.append((rel, 0, 'not-readable-utf8'))
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            for category, pattern in PATTERNS.items():
                if pattern.search(line):
                    errors.append((rel, line_no, category))
        if path.suffix == '.py':
            try:
                ast.parse(content)
            except SyntaxError:
                errors.append((rel, 0, 'python-syntax-error'))
    return files, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', action='store_true')
    args = parser.parse_args()
    files, errors = audit()
    for file, line, category in errors:
        print(f'{file}:{line}: {category}')
    if errors:
        raise SystemExit(f'Release audit failed: {len(errors)} finding(s).')
    if args.manifest:
        target = ROOT / 'docs' / 'release-manifest.json'
        manifest = {
            'schema': 1,
            'notice': 'Integrity inventory, not proof of authorship, safety or historic date. Excludes itself.',
            'files': [{
                'path': str(path.relative_to(ROOT)),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'bytes': path.stat().st_size,
            } for path in files if path != target],
        }
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'PASS: {len(files)} allowlisted UTF-8 files; no configured secret/path pattern found.')
    print('This is a bounded pattern check, not a guarantee of no secrets or vulnerabilities.')


if __name__ == '__main__':
    main()
