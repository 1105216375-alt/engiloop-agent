from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


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
