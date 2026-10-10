"""Which domain a command uses: flag, then QUARRY_DOMAIN, then quarry.toml, then core (spec §8.1)."""
from pathlib import Path

import pytest

from quarry import catalog, lens
from quarry.errors import QuarryError

CAT = catalog.load()


def test_default_is_core(tmp_path):
    assert lens.resolve_domain(None, {}, None, tmp_path, CAT) == "core"


def test_precedence(tmp_path):
    (tmp_path / "quarry.toml").write_text('domain = "biology"\n')
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert lens.resolve_domain(None, {}, None, sub, CAT) == "biology"            # nearest file above
    assert lens.resolve_domain(None, {"QUARRY_DOMAIN": "core"}, None, sub, CAT) == "core"
    assert lens.resolve_domain("biology", {"QUARRY_DOMAIN": "core"}, None, sub, CAT) == "biology"


def test_explicit_config_wins_over_discovery(tmp_path):
    (tmp_path / "quarry.toml").write_text('domain = "biology"\n')
    other = tmp_path / "other.toml"
    other.write_text('domain = "core"\n')
    assert lens.resolve_domain(None, {}, other, tmp_path, CAT) == "core"


@pytest.mark.parametrize("setup, kind", [
    (lambda p: None, "config_not_found"),
    (lambda p: p.write_text('domain = "biology"\ncolour = 1\n'), "config_invalid"),
    (lambda p: p.write_text("domain = [\n"), "config_invalid"),
])
def test_bad_explicit_config(tmp_path, setup, kind):
    path = tmp_path / "q.toml"
    setup(path)
    with pytest.raises(QuarryError) as caught:
        lens.resolve_domain(None, {}, path, tmp_path, CAT)
    assert caught.value.kind == kind


def test_unknown_domain(tmp_path):
    with pytest.raises(QuarryError) as caught:
        lens.resolve_domain("physics", {}, None, tmp_path, CAT)
    assert caught.value.kind == "unknown_domain"


@pytest.mark.parametrize('text', ['domain = 1\n', 'domain = true\n', 'colour = 1\n'])
def test_invalid_explicit_config_is_validated_even_when_flag_wins(tmp_path, text):
    path = tmp_path / 'q.toml'
    path.write_text(text)
    with pytest.raises(QuarryError) as caught:
        lens.resolve_domain('core', {}, path, tmp_path, CAT)
    assert caught.value.kind == 'config_invalid'


def test_nearest_config_wins(tmp_path):
    (tmp_path / 'quarry.toml').write_text('domain = "biology"\n')
    child = tmp_path / 'child'
    child.mkdir()
    (child / 'quarry.toml').write_text('domain = "core"\n')
    assert lens.resolve_domain(None, {}, None, child, CAT) == 'core'


def test_config_read_error_is_typed(tmp_path, monkeypatch):
    path = tmp_path / 'q.toml'
    path.write_text('domain = "biology"\n')

    def unreadable(self, *args, **kwargs):
        raise PermissionError('config read denied')

    monkeypatch.setattr(Path, 'read_text', unreadable)
    with pytest.raises(QuarryError) as caught:
        lens.resolve_domain(None, {}, path, tmp_path, CAT)
    assert caught.value.kind == 'config_invalid'
