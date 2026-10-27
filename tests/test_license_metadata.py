"""Check that the published license, metadata, and historical notice agree."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_current_license_and_project_metadata_match():
    license_text = (ROOT / 'LICENSE').read_text(encoding='utf-8')
    metadata = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'GNU AFFERO GENERAL PUBLIC LICENSE' in license_text
    assert 'Version 3, 19 November 2007' in license_text
    assert '13. Remote Network Interaction' in license_text
    assert 'license = "AGPL-3.0-only"' in metadata
    assert 'LICENSES/Apache-2.0.txt' in metadata


def test_historical_license_and_attribution_remain_available():
    prior_license = (ROOT / 'LICENSES' / 'Apache-2.0.txt').read_text(encoding='utf-8')
    notice = (ROOT / 'NOTICE').read_text(encoding='utf-8')
    assert 'Apache License' in prior_license
    assert 'Version 2.0, January 2004' in prior_license
    assert 'dd539c86303c47f77a3e4ecbd0082a93b7edb105' in notice
    assert 'original grants remain effective' in notice
    assert 'Copyright 2026 1105216375-alt and contributors' in notice


def test_homepages_link_both_license_texts():
    for name in ('README.md', 'README.zh-CN.md'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert 'AGPL-3.0-only' in text
        assert '(LICENSE)' in text
        assert '(LICENSES/Apache-2.0.txt)' in text
        assert 'dd539c8' in text
