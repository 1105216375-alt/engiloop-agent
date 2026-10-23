from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import struct
import zlib

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location('release_check', ROOT / 'tools' / 'check_release.py')
checker = module_from_spec(spec)
spec.loader.exec_module(checker)


def test_public_tree_has_no_configured_pattern_findings():
    files, errors = checker.audit()
    assert files
    assert not errors, errors


def test_scanner_rejects_non_public_files_and_secrets(tmp_path):
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'bad.md').write_text('sk-' + 'a' * 40)
    (tmp_path / 'archive.db').write_bytes(b'private')
    _, errors = checker.audit(tmp_path)
    assert {item[2] for item in errors} == {'provider-token', 'file-not-allowlisted'}


def test_scanner_rejects_symlinks(tmp_path):
    (tmp_path / 'README.md').symlink_to('/nonexistent-public-test-target')
    _, errors = checker.audit(tmp_path)
    assert errors == [('README.md', 0, 'symlink-not-allowed')]


def png_chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))


def minimal_png(extra=b''):
    return (b'\x89PNG\r\n\x1a\n'
            + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + extra + png_chunk(b'IDAT', zlib.compress(b'\0\0\0\0'))
            + png_chunk(b'IEND', b''))


def test_scanner_accepts_only_named_static_visuals_and_chinese_readme(tmp_path):
    assets = tmp_path / 'docs' / 'assets'
    assets.mkdir(parents=True)
    (assets / 'hero.png').write_bytes(minimal_png())
    for name in ('execution-contract.svg', 'residual-intake.svg'):
        (assets / name).write_text('<svg xmlns="http://www.w3.org/2000/svg"><title>Example</title><rect width="10" height="10"/></svg>')
    (tmp_path / 'README.zh-CN.md').write_text('中文入口', encoding='utf-8')
    files, errors = checker.audit(tmp_path)
    assert len(files) == 4
    assert not errors
    (assets / 'private-photo.png').write_bytes(minimal_png())
    _, errors = checker.audit(tmp_path)
    assert errors == [('docs/assets/private-photo.png', 0, 'file-not-allowlisted')]


@pytest.mark.parametrize('content', [
    '<svg><script>void(0)</script></svg>',
    '<svg onload="void(0)"/>',
    '<svg><foreignObject/></svg>',
    '<svg><use href="https://example.com/private.svg"/></svg>',
    '<svg><style>rect {fill: url(https://example.com/image.png)}</style></svg>',
    '<svg><style>@import "https://example.com/theme.css";</style></svg>',
    '<!DOCTYPE svg><svg/>',
    '<?xml-stylesheet href="https://example.com/theme.css"?><svg/>',
])
def test_scanner_rejects_active_or_external_svg(tmp_path, content):
    assets = tmp_path / 'docs' / 'assets'
    assets.mkdir(parents=True)
    (assets / 'execution-contract.svg').write_text(content)
    _, errors = checker.audit(tmp_path)
    assert errors
    assert errors[0][2].startswith('svg-')


def test_scanner_keeps_text_pattern_checks_for_svg(tmp_path):
    assets = tmp_path / 'docs' / 'assets'
    assets.mkdir(parents=True)
    (assets / 'residual-intake.svg').write_text('<svg><desc>' + 'sk-' + 'x' * 40 + '</desc></svg>')
    _, errors = checker.audit(tmp_path)
    assert errors[0][2] == 'provider-token'


@pytest.mark.parametrize('data', [
    b'not an image',
    minimal_png()[:-1],
    minimal_png() + b'unreviewed trailing bytes',
    minimal_png(png_chunk(b'tEXt', b'Comment\0not allowed')),
    minimal_png(png_chunk(b'eXIf', b'not allowed')),
    minimal_png(png_chunk(b'iCCP', b'not allowed')),
])
def test_scanner_rejects_malformed_or_metadata_bearing_png(tmp_path, data):
    assets = tmp_path / 'docs' / 'assets'
    assets.mkdir(parents=True)
    (assets / 'hero.png').write_bytes(data)
    _, errors = checker.audit(tmp_path)
    assert errors


def test_png_crc_and_dimensions_are_checked():
    data = bytearray(minimal_png())
    data[20] ^= 1
    assert checker.png_findings(bytes(data)) == ['invalid-png-crc']
    oversized = (b'\x89PNG\r\n\x1a\n'
                 + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 9000, 1, 8, 2, 0, 0, 0))
                 + png_chunk(b'IDAT', zlib.compress(b'\0\0\0\0'))
                 + png_chunk(b'IEND', b''))
    assert checker.png_findings(oversized) == ['invalid-png-dimensions-or-format']


def test_named_hero_may_preserve_bounded_c2pa_provenance(tmp_path):
    assets = tmp_path / 'docs' / 'assets'
    assets.mkdir(parents=True)
    data = minimal_png(png_chunk(b'caBX', b'public generated-asset provenance'))
    (assets / 'hero.png').write_bytes(data)
    _, errors = checker.audit(tmp_path)
    assert not errors
    # Generic PNG validation does not grant this exception.
    assert checker.png_findings(data) == ['png-metadata-or-chunk-not-allowed']
    oversized = minimal_png(png_chunk(b'caBX', b'x' * (128 * 1024 + 1)))
    assert checker.png_findings(oversized, allow_c2pa=True) == ['png-provenance-too-large-or-duplicate']


def test_c2pa_container_is_scanned_for_configured_secret_patterns():
    data = minimal_png(png_chunk(b'caBX', b'example ' + b'sk-' + b'x' * 40))
    assert checker.png_findings(data, allow_c2pa=True) == ['png-provenance-provider-token']
