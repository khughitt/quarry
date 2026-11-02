"""Skill resource notes are generated from the catalog between markers; hand-written text survives."""
import json
import shutil
import textwrap

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import skills
from quarry.cli import main
from quarry.errors import QuarryError

KEYED = """
id = "keyed"
name = "Keyed Archive"
roles = ["repository", "literature-index"]
prefixes = ["keyed"]
adapter = "quarry.adapters"

[[routes]]
kind = "rest"
url = "https://example.org/api"
min_interval = 0.5
keyed_min_interval = 0.1
service = "example"
auth = "optional-key"
key_env = "NCBI_API_KEY"
key_param = "api_key"
reports_total = true
page_cap = 500

[[pitfalls]]
summary = "counts include withdrawn records"
handling = "withdrawn records are dropped and reported in warnings"
evidence = "SECRET-EVIDENCE-TEXT"
"""

OTHER = KEYED.replace('"keyed"', '"other"').replace("Keyed Archive", "Other Archive") \
    .replace("optional-key", "none").replace('key_env = "NCBI_API_KEY"\n', "").replace('key_param = "api_key"\n', "")

DOMAINS = {
    "core": 'id = "core"\nsummary = "shared"\nresources = ["other"]\nsearch_default = ["other"]\n',
    "lab": 'id = "lab"\nsummary = "lab"\nincludes = ["core"]\nresources = ["keyed"]\nsearch_default = ["keyed"]\n',
}


@pytest.fixture
def synthetic(tmp_path):
    root = tmp_path / "catalog"
    for sub, files in (("resources", {"keyed": KEYED, "other": OTHER}), ("domains", DOMAINS)):
        (root / sub).mkdir(parents=True)
        for name, text in files.items():
            (root / sub / f"{name}.toml").write_text(textwrap.dedent(text))
    return catalog_mod.load(root)


def skill_file(root, name, body="Intro text.\n"):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\n---\n{body}\n{skills.START}\n{skills.END}\n\nOutro text.\n")
    return path


def test_render_notes_lists_routes_and_pitfalls(synthetic):
    notes = skills.render_notes(synthetic, "lab")
    assert "### Keyed Archive (`keyed`)" in notes
    assert "every 0.5 s" in notes and "key NCBI_API_KEY" in notes
    assert "counts include withdrawn records. Handling: withdrawn records are dropped and reported in warnings." in notes
    assert "SECRET-EVIDENCE-TEXT" not in notes


def test_core_notes_go_to_the_quarry_skill(tmp_path):
    assert skills.skill_path(tmp_path, "core") == tmp_path / "quarry" / "SKILL.md"
    assert skills.skill_path(tmp_path, "biology") == tmp_path / "quarry-biology" / "SKILL.md"


def test_domain_notes_skip_included_resources(synthetic):
    assert "Keyed Archive" in skills.render_notes(synthetic, "lab")
    assert "Other Archive" not in skills.render_notes(synthetic, "lab")
    real = catalog_mod.load()
    notes = skills.render_notes(real, "biology")
    assert "(`geo`)" in notes and "(`uberon`)" in notes and "(`zenodo`)" not in notes


def test_build_replaces_only_between_markers(synthetic, tmp_path):
    root = tmp_path / "skills"
    lab = skill_file(root, "quarry-lab", body="Keep\tthis  exactly. \n")
    core = skill_file(root, "quarry")
    before = lab.read_text()
    changed, unchanged = skills.build(synthetic, root)
    assert sorted(changed) == sorted([lab, core]) and unchanged == []
    after = lab.read_text()
    head, tail = before.split(skills.END)[0].split(skills.START)[0], before.split(skills.END)[1]
    assert after.startswith(head + skills.START) and after.endswith(skills.END + tail)
    assert "Keyed Archive" in after
    assert skills.build(synthetic, root) == ([], sorted([lab, core]))


def test_build_reports_missing_and_unmarked(synthetic, tmp_path):
    root = tmp_path / "skills"
    skill_file(root, "quarry")
    with pytest.raises(QuarryError) as missing:
        skills.build(synthetic, root)
    assert missing.value.kind == "skill_missing" and str(root / "quarry-lab" / "SKILL.md") in missing.value.detail
    lab = skill_file(root, "quarry-lab")
    lab.write_text(lab.read_text() + f"{skills.START}\n")
    with pytest.raises(QuarryError) as unmarked:
        skills.build(synthetic, root)
    assert unmarked.value.kind == "skill_unmarked"
    with pytest.raises(QuarryError) as absent:
        skills.build(synthetic, tmp_path / "nowhere")
    assert absent.value.kind == "no_checkout"


def test_every_domain_has_a_skill():
    skills.build(catalog_mod.load(), skills.SKILLS_ROOT, write=False)


def test_checked_in_skills_are_current():
    assert skills.build(catalog_mod.load(), skills.SKILLS_ROOT, write=False)[0] == []


def test_skills_build_cli_is_idempotent(tmp_path, monkeypatch):
    root = tmp_path / "checkout" / "skills"
    shutil.copytree(skills.SKILLS_ROOT, root)
    monkeypatch.setattr(skills, "SKILLS_ROOT", root)
    path = skills.skill_path(root, "biology")
    text = path.read_text()
    start, end = text.index(skills.START) + len(skills.START), text.index(skills.END)
    path.write_text(text[:start] + text[end:])
    first = CliRunner().invoke(main, ["skills", "build", "--pretty"], catch_exceptions=False)
    assert first.exit_code == 0 and first.stdout.strip() == "wrote skills/quarry-biology/SKILL.md"
    second = CliRunner().invoke(main, ["skills", "build", "--pretty"], catch_exceptions=False)
    assert second.exit_code == 0 and second.stdout.strip() == "skills are current"
    third = CliRunner().invoke(main, ["--json", "skills", "build"], catch_exceptions=False)
    assert json.loads(third.stdout)["written"] == []
