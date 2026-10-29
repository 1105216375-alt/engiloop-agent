"""Allowlist and pattern audit. Prints locations/categories, never matched secrets.

This catches a bounded set of patterns, not all secrets, private data or license
problems. Approved visuals receive container/static-content checks, not OCR or
image-content review. --manifest records hashes of allowlisted public files.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zlib

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {'engineering_agent', 'bip_runtime', 'domain', 'nskernel',
               'legacy_v7', 'examples', 'tests', 'docs', 'tools', '.github'}
ROOT_FILES = {'README.md', 'README.zh-CN.md', 'LICENSE', 'NOTICE', 'SECURITY.md', 'CONTRIBUTING.md',
              'MANIFEST.in', 'pyproject.toml', '.gitignore', 'LICENSES/Apache-2.0.txt'}
VISUAL_FILES = {'docs/assets/hero.png', 'docs/assets/hero-engiloop.png', 'docs/assets/execution-contract.svg',
                'docs/assets/residual-intake.svg'}
MAX_VISUAL_BYTES = 10 * 1024 * 1024
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
        rel_name = rel.as_posix()
        if any(part in IGNORED or part.endswith('.egg-info') for part in rel.parts):
            continue
        if path.name == '.DS_Store':
            continue
        if path.is_symlink():
            errors.append((rel_name, 0, 'symlink-not-allowed'))
            continue
        if not path.is_file():
            continue
        if rel_name not in ROOT_FILES | VISUAL_FILES and not (
            rel.parts[0] in DIRECTORIES and path.suffix in EXTENSIONS
        ):
            errors.append((rel_name, 0, 'file-not-allowlisted'))
            continue
        files.append(path)
    return files, errors


def png_findings(data, *, allow_c2pa=False):
    """Bounded PNG container checks; does not decode or inspect image pixels."""
    if len(data) > MAX_VISUAL_BYTES:
        return ['visual-too-large']
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        return ['invalid-png']
    offset, chunks = 8, []
    permitted = {b'IHDR', b'PLTE', b'IDAT', b'IEND', b'tRNS', b'gAMA',
                 b'cHRM', b'sRGB', b'pHYs', b'sBIT', b'bKGD'}
    fixed_lengths = {b'IHDR': 13, b'IEND': 0, b'gAMA': 4, b'cHRM': 32,
                     b'sRGB': 1, b'pHYs': 9}
    while offset < len(data):
        if offset + 12 > len(data):
            return ['invalid-png']
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data):
            return ['invalid-png']
        payload = data[offset + 8:offset + 8 + size]
        checksum = struct.unpack('>I', data[offset + 8 + size:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != checksum:
            return ['invalid-png-crc']
        if kind == b'caBX' and allow_c2pa:
            # Preserve generated-asset C2PA provenance, only for the named hero.
            # This is a bounded byte/pattern check, not signature verification.
            if size > 128 * 1024 or kind in chunks:
                return ['png-provenance-too-large-or-duplicate']
            text = payload.decode('utf-8', errors='replace')
            findings = ['png-provenance-' + category for category, pattern
                        in PATTERNS.items() if pattern.search(text)]
            if findings:
                return findings
        elif kind not in permitted:
            # No text, EXIF, ICC profile or arbitrary ancillary metadata.
            return ['png-metadata-or-chunk-not-allowed']
        if kind in fixed_lengths and size != fixed_lengths[kind]:
            return ['invalid-png']
        if not chunks and kind != b'IHDR':
            return ['invalid-png']
        if kind == b'IHDR':
            if chunks:
                return ['invalid-png']
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            valid_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16},
                            3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if (not 0 < width <= 8192 or not 0 < height <= 8192
                    or width * height > 33554432
                    or depth not in valid_depths.get(color, set())
                    or compression != 0 or filtering != 0 or interlace not in (0, 1)):
                return ['invalid-png-dimensions-or-format']
        if kind == b'PLTE' and (not 0 < size <= 768 or size % 3):
            return ['invalid-png']
        if kind in {b'sBIT', b'bKGD', b'tRNS'} and size > 256:
            return ['invalid-png']
        chunks.append(kind)
        offset = end
        if kind == b'IEND':
            return [] if offset == len(data) and b'IDAT' in chunks else ['invalid-png']
    return ['invalid-png']


def svg_findings(content):
    """Permit static inline SVG drawing primitives, without external content."""
    if len(content.encode('utf-8')) > MAX_VISUAL_BYTES:
        return ['visual-too-large']
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)|<\?xml-stylesheet', content, re.I):
        return ['svg-declaration-not-allowed']
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return ['invalid-svg']
    allowed = {'svg', 'g', 'defs', 'title', 'desc', 'style', 'rect', 'circle',
               'ellipse', 'line', 'polyline', 'polygon', 'path', 'text', 'tspan',
               'textPath', 'linearGradient', 'radialGradient', 'stop', 'pattern',
               'clipPath', 'mask', 'marker', 'symbol', 'use', 'filter',
               'feGaussianBlur', 'feOffset', 'feFlood', 'feComposite', 'feMerge',
               'feMergeNode', 'feColorMatrix', 'feBlend', 'feDropShadow'}
    svg_namespace = '{http://www.w3.org/2000/svg}'
    if root.tag not in {'svg', svg_namespace + 'svg'}:
        return ['invalid-svg']
    for element in root.iter():
        tag = element.tag.removeprefix(svg_namespace)
        if tag not in allowed:
            return ['svg-element-not-allowed']
        for key, value in element.attrib.items():
            local_key = key.rsplit('}', 1)[-1].lower()
            if local_key.startswith('on') or local_key == 'base':
                return ['svg-active-attribute-not-allowed']
            if local_key == 'href' and not re.fullmatch(r'#[A-Za-z_][\w:.-]*', value):
                return ['svg-external-resource-not-allowed']
        values = list(element.attrib.values())
        if tag == 'style':
            values.append(element.text or '')
        for value in values:
            if re.search(r'@import|expression\s*\(|javascript\s*:|\\', value, re.I):
                return ['svg-active-style-not-allowed']
            for target in re.findall(r'url\s*\((.*?)\)', value, re.I | re.S):
                target = target.strip().strip('\"\'')
                if not re.fullmatch(r'#[A-Za-z_][\w:.-]*', target):
                    return ['svg-external-resource-not-allowed']
    return []


def audit(root=ROOT):
    files, errors = public_files(root)
    for path in files:
        rel = path.relative_to(root).as_posix()
        if path.suffix == '.png':
            try:
                if path.stat().st_size > MAX_VISUAL_BYTES:
                    findings = ['visual-too-large']
                else:
                    findings = png_findings(path.read_bytes(), allow_c2pa=(rel in {'docs/assets/hero.png', 'docs/assets/hero-engiloop.png'}))
            except OSError:
                findings = ['visual-not-readable']
            errors.extend((rel, 0, finding) for finding in findings)
            continue
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
        if path.suffix == '.svg':
            errors.extend((rel, 0, finding) for finding in svg_findings(content))
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
                'path': path.relative_to(ROOT).as_posix(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'bytes': path.stat().st_size,
            } for path in files if path != target],
        }
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'PASS: {len(files)} allowlisted files; configured text patterns and approved visual checks passed.')
    print('Bounded checks only: no image OCR, content certification or guarantee of no secrets/vulnerabilities.')


if __name__ == '__main__':
    main()
