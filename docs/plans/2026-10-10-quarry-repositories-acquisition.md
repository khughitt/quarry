# quarry repositories and acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the five remaining first-version repositories (GEO, BioStudies, Zenodo, figshare,
PubMed) behind the search framework, and the `files` command with `--acquisition`, so a
biology search covers GEO, ENA, BioStudies, Zenodo and figshare and a record's files can be
handed to the kernel's acquisition.

**Architecture:** Each resource is one adapter module following ENA's shape: a pure
`prepare(query)` that compiles and refuses before any HTTP, an execution callable that pages
within the route's cap and proves completeness, `show` and `files` over the record's own
endpoints. A small `files` layer (the `File` record, glob selection) and an `acquisition`
module (the kernel's request, http(s)-only, unique names, sha256 only) sit between the
adapters and the new CLI command. GEO and PubMed share one E-utilities helper and one
`ncbi-eutils` throttle. Tests replay recorded exchanges; recording stays a deliberate act.

**Tech Stack:** Python ≥ 3.13, uv, click ≥ 8.1, httpx ≥ 0.28, pytest ≥ 8.4; ops' vendored
`tt`, `cli.toml`, `cli_surface.py`, `vendored`.

**Spec:** `docs/specs/2026-10-10-quarry-domain-layers-design.md` in the science repository
(task `sci-ba7150`; approved 2026-10-10). Read it before any task; `§n` below is its section.
This is plan 2 of 3 named in the foundation plan (science `docs/plans/2026-10-10-quarry-foundation.md`);
plan 3 (terms, skills, live checks) is `quarry-06c10c`.

## Paths

- `$TREE` is the shared tree holding the quarry, science and ops checkouts:
  `dirname "$(tasks root quarry-3f4c3b | python3 -c 'import json,sys; print(json.load(sys.stdin)["root"])')"`.
- quarry work happens in `$WT` = `$TREE/quarry/.worktrees/adapters-acquisition`, the task's
  worktree (branch `adapters-acquisition`, already created, set up and claimed). Every path
  below is relative to it unless the task names ops.
- Task 1 works in an ops worktree, `$TREE/ops/.worktrees/quarry-files`.
- Run every test through the front door: `just test-one <pytest args>` for the test at hand,
  `just test-fast` before each commit. Never call `pytest` directly.

## What the probes settled (2026-10-10, read-only, live)

The plan was written against these observations; an implementer who sees a service behave
differently while recording stops and notes it on the task rather than guessing.

| Resource | Search endpoint and total | Dates | Organism | `show` | `files` and checksum | Auth, rate, page cap |
|---|---|---|---|---|---|---|
| GEO | E-utilities `esearch.fcgi` db=gds (`count`) + `esummary.fcgi` POST by uid | `("Y/M/D"[PDAT] : "Y/M/D"[PDAT])` | `"Homo sapiens"[ORGN]`; `txid9606[ORGN]` gives the same count | esearch `GSEn[ACCN] AND gse[ETYP]` then esummary; brief SOFT text (`acc.cgi?targ=self&form=text&view=brief`) adds `!Series_relation` and `!Series_sample_taxid` | brief text `!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/…`, served at `https://ftp.ncbi.nlm.nih.gov/…`; `suppl/filelist.txt` is not universal (404 for GSE193531); no checksum published | optional `NCBI_API_KEY` as `api_key`; 0.34 s, keyed 0.1 s; esearch serves at most 10,000 ids |
| PubMed | same E-utilities, db=pubmed; `count` | `[PDAT]` range | none | esummary by uid; a missing uid answers `{"error": …}` under HTTP 200 | none: a publication has no files | as GEO; same `ncbi-eutils` throttle |
| BioStudies | `api/v1/search?query=…&pageSize=100&page=n`; `totalHits` with `isTotalHitsExact` (false on large sets) | `release_date=[a TO b]` parameter, also valid inside `query` | `facet.organism=<name>` parameter; names only; hits carry no organism | `api/v1/studies/<acc>`: `attributes` (ReleaseDate), `section.attributes` (Title, Description, Organism), `section.links` (type GEO), files in `section.files` or nested `subsections[].files` (lists of lists); 404 `Study not found` | `api/v1/studies/<acc>/info` → `httpLink`; a file is `<httpLink>/Files/<path>` (200); `path`, `size`, `type` (`file` or `directory`); no checksum | none; 0.5 s; 100 per page |
| Zenodo | `api/records?q=…&type=dataset&size=25&page=n&sort=mostrecent`; `hits.total` | `publication_date:[a TO b]` inside `q` | none | `api/records/<id>`; 404 | same JSON `files[]`: `key`, `size`, `checksum: "md5:…"`, `links.self` (https `…/content`) | optional `ZENODO_TOKEN` as `access_token`; 1.0 s; size > 25 is HTTP 400 unauthenticated; a 10,000-result window |
| figshare | POST `v2/articles/search` `{search_for, item_type: 3, page_size, page, order, order_direction}`; no total; `published_since`/`published_until` are **not applied** (2026 items came back under `published_until: 2020-01-01`) | client-side on `published_date` with `order=published_date`: `desc` and stop below `--since`; `asc` and stop above `--until` when only it is given | none | `v2/articles/<id>`; 404 `EntityNotFound` | same JSON `files[]`: `name`, `size`, `download_url` (https), `computed_md5`, `mimetype`, `is_link_only` | none; 1.0 s; 100 per page |
| ENA (files only here) | existing | existing | existing | existing | `filereport?accession=<PRJ or SRP>&result=read_run&fields=run_accession,fastq_ftp,fastq_md5,fastq_bytes,submitted_ftp,submitted_md5,submitted_bytes,sra_ftp,sra_md5,sra_bytes&format=json&limit=0`; `;`-separated paths under `ftp.sra.ebi.ac.uk/vol1/…`, served at `https://ftp.sra.ebi.ac.uk/vol1/…`; md5 only | existing |

Consequences fixed here, not open for the implementer:

- No first-version resource publishes sha256, so `--acquisition` never sets `expected` for
  them; the builder still emits it when an adapter reports `sha256`, and a test proves it.
- BioStudies totals are trusted only when `isTotalHitsExact` is true; otherwise the listing
  pages to a short page and coverage reports `total: null`, `complete: "short-page"`.
- BioStudies `--organism` takes a taxon name only; an `ncbitaxon:` CURIE is refused there
  (`unsupported_filter`), before any request, as §8.2 requires for a filter the resource
  cannot express.
- figshare compiles `--since`/`--until` as ordering plus a client-side bound; nothing is
  dropped. Its coverage `native` is the `search_for` string, and the handling is a pitfall.
- A count beyond E-utilities' 10,000-id window (GEO, PubMed) or Zenodo's 10,000-result
  window is refused with `too_many_results` before any summary request, unless `-n` keeps
  the listing inside it.
- GEO covers series (`GSE`) only; `geo:GSM…`, `geo:GPL…`, `geo:GDS…` are refused with
  `unsupported_id` before any request.
- `files` on PubMed is `unsupported_role`: a publication carries no files (§4.3).

## Global Constraints

- Python `>=3.13`; dependencies stay `click>=8.1`, `httpx>=0.28`; no new runtime dependency.
- Every id quarry prints is a CURIE with Bioregistry's canonical prefix (§7): `geo:`, `pubmed:`, `pmc:`, `zenodo.record:`, `biostudies:`, `arrayexpress:`, `bioproject:`, `insdc.sra:`, `doi:`, `ncbitaxon:`; `figshare:` is a declared `local_prefix` (Bioregistry has none).
- Roles are exactly `identifier-scheme`, `vocabulary`, `ontology`, `data-model`, `repository`, `knowledgebase`, `literature-index`; route kinds exactly `rest`, `query-endpoint`, `bulk`, `local-tool` (§4).
- Every static refusal happens in `prepare(query)` or before the adapter's first request; a selected adapter never sends while another will refuse (foundation ruling).
- Output follows the ops CLI conventions: `--json` emits one JSON value on stdout; a failure under `--json` prints `{"error": {"kind", "detail"}}` on stderr and nothing on stdout; exits are 0, 1, 2 (§8).
- A new command or option is a row in ops `cli.toml` first, then `vendored adopt cli.toml`; the conformance test fails on any difference (quarry AGENTS.md).
- A listing shorter than the resource's reported total is a failure, never a result; `-n` reports `limited` (§8.3).
- `publication` is one CURIE kept apart from `xrefs`; when a record names several papers, the first is `publication`, the rest go to `xrefs`, and the search report carries a warning (§8.3).
- Tests never reach the network; fixtures are recorded with `just record <resource>`, matched on method, path, sorted query and body (§12).
- A credential never reaches a trace line, an error detail, or a recorded fixture; key variables keep each service's own name (`NCBI_API_KEY`, `ZENODO_TOKEN`) (§9).
- quarry downloads no bytes and makes no kernel acts; `files --acquisition` emits the request only (§9).
- `files --acquisition` emits `title`, `locator = accession:<CURIE>`, and `resources[]` of unique `name`, http(s) `url`, optional `expected = sha256:<hex>`; a file without an http(s) location fails with `unsupported_transport` naming it (§9).
- Commit messages follow conventional commits; no AI attribution.

## Review Focus

Inputs the spec implies but no task's tests exercised before this list was written, most
likely to bite first. Each line names the owning task, whose steps now include the test.

1. A BioStudies file whose `path` carries a space or a non-ASCII character: `--acquisition` must emit a percent-encoded URL the kernel's URL check accepts (it refuses whitespace and non-ASCII bytes). Task 5.
2. A GEO series whose brief text lists no `!Series_supplementary_file`: `files` prints an empty list and exits 0; `--acquisition` fails with `no_files`. Task 3.
3. A figshare item with a link-only file (`is_link_only: true`): `files` lists it with no URL; `--acquisition` fails with `unsupported_transport` naming it, and `--file` excluding it succeeds. Task 7.
4. A bare numeric accession once `pubmed`, `zenodo.record` and `figshare` are owned (`id normalize 12345`, `show 12345`): `ambiguous_id` naming all three, not a guess. Task 8.
5. A PubMed or GEO count above 10,000 without `-n`: `too_many_results` before any esummary request; with `-n 50` the listing is `limited`. Task 3 (the shared helper's tests and the GEO CLI test).

---

### Task 1: The `files` rows in ops's CLI table

**Files:**
- Modify: `$TREE/ops/cli.toml` (one command row under `# ---- quarry`)

**Interfaces:**
- Produces: the `files` row Task 2's `vendored adopt` brings into `tools/cli.toml`, which `tests/test_cli_surface.py` compares with the click surface. Task 2 builds exactly this argument and these options; the summary becomes the command's docstring.

- [ ] **Step 1: Create the ops worktree**

```bash
cd "$TREE/ops"
work-link --ensure .worktrees
git worktree add -b quarry-files .worktrees/quarry-files
git worktree lock --reason "on WORK_ROOT storage (host: $(uname -n))" .worktrees/quarry-files
cd .worktrees/quarry-files && just setup
```

- [ ] **Step 2: Add the row**

In `cli.toml`, after the `[[cli.quarry.commands]]` row whose `path = ["show"]` and before
`# ---- science`, add:

```toml
[[cli.quarry.commands]]
path = ["files"]
summary = "List a record's files, or the acquisition request the kernel takes for them"
args = [
  { name = "curie", value = "string", required = true },
]
options = [
  { names = ["--file"], value = "string", repeatable = true },
  { names = ["--acquisition"], value = "none" },
]
```

`--file` is a local repeatable option without a role, like `search --in`; repeats widen.

- [ ] **Step 3: Validate the table**

```bash
just test-one tests.test_cli
just check
```

Expected: the unittest run passes (`test_file_is_valid`, `test_row_shape_rules` cover the new
row); `check` prints nothing.

- [ ] **Step 4: Commit and land on ops main**

```bash
git add cli.toml
git commit -m "feat(cli): quarry files command rows"
cd "$TREE/ops" && git merge --ff-only quarry-files
```

If `--ff-only` refuses because ops main moved, rebase the branch on main in the worktree and
merge again. ops is a personal-profile checkout, so the local merge is a bounded decision;
no push happens here.

- [ ] **Step 5: Remove the ops worktree**

```bash
cd "$TREE/ops/.worktrees/quarry-files" && tt-report
git -C "$TREE/ops/.worktrees/quarry-files" status --ignored --porcelain   # expect nothing worth keeping
cd "$TREE/ops" && git worktree unlock .worktrees/quarry-files && git worktree remove .worktrees/quarry-files
git branch -d quarry-files
```

### Task 2: The files layer, the acquisition request and the `files` command, proven on ENA

**Files:**
- Create: `src/quarry/files.py`, `src/quarry/acquisition.py`, `tests/test_files.py`, `tests/fixtures/ena/files-first.json` (recorded)
- Modify: `src/quarry/http.py` (`accept`), `src/quarry/results.py` (`Coverage.warnings`), `src/quarry/search.py` (collect them), `src/quarry/adapters/base.py` (`files` in the protocol), `src/quarry/adapters/ena.py` (`files`), `src/quarry/cli.py` (`files` command, shared owner lookup), `tests/fake_adapter.py`, `tests/test_search.py`, `tests/test_http.py`, `tests/test_ena.py`, `tests/recordings/ena.py`, `tests/test_cli_surface.py`, `tools/cli.toml` (by `vendored adopt`)

**Interfaces:**
- Consumes: Task 1's row; `Client.request`, `Result`, `Coverage`, `Curie`, `QuarryError`, `UpstreamError`, `completeness`, `ReplayTransport`, `exchange`, `replay_client`, `context.make_client`.
- Produces, for every later task:
  - `quarry.files.File(name: str, urls: tuple[str, ...] = (), size: int | None = None, checksums: Mapping[str, str] = {}, media_type: str | None = None)` with `as_dict()`; `quarry.files.select(files: list[File], globs: tuple[str, ...]) -> list[File]`; `quarry.files.http_url(file: File) -> str | None`.
  - `quarry.acquisition.build(record: Result, curie: Curie, files: list[File]) -> dict`.
  - `Adapter.files(client: Client, curie: Curie) -> tuple[Result, list[File]]`: the record and its files; a missing record raises `not_found`.
  - `Coverage(..., error=None, warnings: tuple[str, ...] = ())`; `search.run` appends each coverage's warnings to the report's `warnings`.
  - `Client.request(route, method, url, *, params=None, json=None, data=None, accept: frozenset[int] = frozenset())`: a status in `accept` is returned to the caller instead of raising `UpstreamError`.
  - `cli._owned(env, text: str) -> tuple[Curie, Resource]`: parses the CURIE and finds its owning data-holding resource (`no_owner`, `unsupported_role`), used by `show` and `files`.

- [ ] **Step 1: Write the failing tests for the files layer and the request builder**

`tests/test_files.py` (first part):

```python
"""The files layer and the acquisition request (spec §8, §9), through fake adapters and the CLI."""
import json

import pytest
from click.testing import CliRunner

from quarry import acquisition
from quarry import files as files_mod
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.files import File
from quarry.ids import Curie
from quarry.results import Result
from tests import fake_adapter

A = File("a.txt", urls=("https://alpha.example/a.txt", "ftp://alpha.example/a.txt"), size=3,
         checksums={"sha256": "ab" * 32}, media_type="text/plain")
B = File("b.bin", urls=("ftp://alpha.example/b.bin",))
C = File("c.csv", urls=("https://alpha.example/c.csv",), checksums={"md5": "0" * 32})
RECORD = Result(id="geo:GSE5", resource="alpha", kind="dataset", title="five")
CURIE = Curie("geo", "GSE5")


def test_select_matches_names_case_sensitively_and_widens_over_globs():
    assert files_mod.select([A, B, C], ()) == [A, B, C]
    assert files_mod.select([A, B, C], ("*.txt",)) == [A]
    assert files_mod.select([A, B, C], ("*.TXT",)) == []
    assert files_mod.select([A, B, C], ("*.txt", "c.*")) == [A, C]


def test_http_url_prefers_https_then_http_and_skips_the_rest():
    assert files_mod.http_url(A) == "https://alpha.example/a.txt"
    assert files_mod.http_url(File("x", urls=("ftp://h/x", "http://h/x"))) == "http://h/x"
    assert files_mod.http_url(B) is None


def test_file_as_dict_omits_unknowns():
    assert B.as_dict() == {"name": "b.bin", "urls": ["ftp://alpha.example/b.bin"]}
    assert A.as_dict() == {"name": "a.txt", "urls": ["https://alpha.example/a.txt", "ftp://alpha.example/a.txt"],
                           "size": 3, "checksums": {"sha256": "ab" * 32}, "media_type": "text/plain"}


def test_build_emits_title_locator_and_resources_with_sha256_only():
    request = acquisition.build(RECORD, CURIE, [A, C])
    assert request == {"title": "five", "locator": "accession:geo:GSE5",
                       "resources": [{"name": "a.txt", "url": "https://alpha.example/a.txt", "expected": "sha256:" + "ab" * 32},
                                     {"name": "c.csv", "url": "https://alpha.example/c.csv"}]}


def test_build_titles_an_untitled_record_by_its_curie():
    untitled = Result(id="geo:GSE5", resource="alpha", kind="dataset", title="")
    assert acquisition.build(untitled, CURIE, [C])["title"] == "geo:GSE5"


@pytest.mark.parametrize("files, kind, named", [
    ([], "no_files", "geo:GSE5"),
    ([A, A], "duplicate_file_name", "a.txt"),
    ([A, B], "unsupported_transport", "b.bin"),
    ([File("sp.txt", urls=("https://h/a b.txt",))], "unsupported_transport", "sp.txt"),
    ([File("u.txt", urls=("https://h/é.txt",))], "unsupported_transport", "u.txt"),
])
def test_build_refusals_name_the_file(files, kind, named):
    with pytest.raises(QuarryError) as caught:
        acquisition.build(RECORD, CURIE, files)
    assert caught.value.kind == kind and named in caught.value.detail
```

- [ ] **Step 2: Run them to see them fail**

Run: `just test-one tests/test_files.py`
Expected: FAIL at import (`No module named 'quarry.acquisition'`).

- [ ] **Step 3: Write the files layer and the builder**

`src/quarry/files.py`:

```python
"""A record's files (spec §8, §9): what an adapter lists, and how --file narrows the list."""
from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class File:
    name: str
    urls: tuple[str, ...] = ()                     # every location the resource gives, https first when it has one
    size: int | None = None
    checksums: Mapping[str, str] = field(default_factory=dict)   # algorithm -> hex digest
    media_type: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"name": self.name, "urls": list(self.urls)}
        if self.size is not None:
            out["size"] = self.size
        if self.checksums:
            out["checksums"] = dict(self.checksums)
        if self.media_type is not None:
            out["media_type"] = self.media_type
        return out


def select(files: list[File], globs: tuple[str, ...]) -> list[File]:
    """The files whose name matches any glob; every file when there is none. Case matters."""
    if not globs:
        return list(files)
    return [f for f in files if any(fnmatch.fnmatchcase(f.name, g) for g in globs)]


def http_url(file: File) -> str | None:
    """The location the kernel can retrieve: https before http; None when the file has neither."""
    for scheme in ("https://", "http://"):
        for url in file.urls:
            if url.startswith(scheme):
                return url
    return None
```

`src/quarry/acquisition.py`:

```python
"""The request the kernel's acquisition takes (spec §9), built from a record and its files.
quarry emits it; the kernel retrieves the bytes and mints the dataset."""
from __future__ import annotations

from quarry.errors import QuarryError
from quarry.files import File, http_url
from quarry.ids import Curie
from quarry.results import Result


def _retrievable(file: File) -> str:
    url = http_url(file)
    if url is None:
        where = ", ".join(file.urls) or "no location"
        raise QuarryError("unsupported_transport",
                          f"{file.name} has no http(s) location ({where}); the kernel retrieves over http and https only")
    if any(c.isspace() or not c.isprintable() or ord(c) > 126 for c in url):
        raise QuarryError("unsupported_transport", f"{file.name}: its URL carries whitespace or a non-ASCII character, "
                                                   f"which the kernel refuses; the adapter must percent-encode it")
    return url


def build(record: Result, curie: Curie, files: list[File]) -> dict:
    if not files:
        raise QuarryError("no_files", f"{curie} has no files to acquire")
    seen: set[str] = set()
    resources = []
    for file in files:
        if file.name in seen:
            raise QuarryError("duplicate_file_name", f"{curie} lists {file.name!r} twice; narrow the list with --file")
        seen.add(file.name)
        resource = {"name": file.name, "url": _retrievable(file)}
        if "sha256" in file.checksums:
            resource["expected"] = f"sha256:{file.checksums['sha256']}"
        resources.append(resource)
    return {"title": record.title or str(curie), "locator": f"accession:{curie}", "resources": resources}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `just test-one tests/test_files.py`
Expected: PASS (7 tests).

- [ ] **Step 5: Add `Coverage.warnings` and `accept`, with their tests**

Append to `tests/test_search.py`:

```python
def test_adapter_warnings_reach_the_report_but_not_the_coverage_object(catalog):
    report = json.loads(invoke("search", "--domain", "biology", "--in", "alpha", "warn").stdout)
    assert report["warnings"] == ["alpha warns"]
    assert "warnings" not in report["coverage"][0]
```

Append to `tests/test_http.py`:

```python
def test_accepted_statuses_return_and_others_still_raise():
    c = client(lambda r: httpx.Response(404))
    assert c.request(route(), "GET", "x", accept=frozenset({404})).status_code == 404
    with pytest.raises(UpstreamError):
        c.request(route(), "GET", "x")
```

In `tests/fake_adapter.py`, make `Fake.prepare`'s `execute` return
`Coverage(self.resource.id, native, match, 3, len(rows), completeness(...), warnings=("alpha warns",) if "warn" in query.phrases else ())`
(the `warnings` keyword is new; `completeness(...)` keeps its arguments).

In `src/quarry/results.py`, add to `Coverage` after `error`:

```python
    warnings: tuple[str, ...] = ()      # what the adapter wants the report to say; never in as_dict
```

In `src/quarry/search.py`, inside `run`'s loop, after `coverage.append(cov)`:

```python
        warnings += list(cov.warnings)
```

In `src/quarry/http.py`, change `request`'s signature and its last check:

```python
    def request(self, route: Route, method: str, url: str, *, params: Mapping | None = None,
                json: object = None, data: Mapping | None = None,
                accept: frozenset[int] = frozenset()) -> httpx.Response:
        """`accept` names error statuses the caller reads itself (a 404 that means not found);
        every other 4xx/5xx is an upstream failure."""
```

and

```python
            if response.status_code >= 400 and response.status_code not in accept:
                raise UpstreamError(f"{method} {shown}: HTTP {response.status_code}")
            return response
```

Run: `just test-one tests/test_search.py tests/test_http.py`
Expected: PASS.

- [ ] **Step 6: Write the failing CLI tests for `files`**

Append to `tests/test_files.py`:

```python
FAKE = """
id = "{id}"
name = "{id}"
roles = ["{role}"]
prefixes = [{prefixes}]
adapter = "tests.fake_adapter"
[[routes]]
kind = "rest"
url = "https://{id}.example"
min_interval = 0
service = "{id}"
auth = "none"
reports_total = true
"""


@pytest.fixture
def catalog(make_catalog):
    fakes = {rid: FAKE.format(id=rid, role=role, prefixes=prefixes)
             for rid, role, prefixes in (("alpha", "repository", '"geo"'), ("onto", "ontology", '"go"'))}
    return make_catalog(fakes, ["alpha", "onto"], ["alpha"])


def invoke(*args):
    return CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)


def test_files_lists_every_location_and_narrows_with_file(catalog, monkeypatch):
    monkeypatch.setattr(fake_adapter, "FILES", [A, B, C])
    out = invoke("files", "GEO:GSE5")
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout) == {"id": "geo:GSE5", "resource": "alpha", "files": [A.as_dict(), B.as_dict(), C.as_dict()]}
    report = json.loads(invoke("files", "geo:GSE5", "--file", "*.txt", "--file", "c.*").stdout)
    assert [f["name"] for f in report["files"]] == ["a.txt", "c.csv"]
    assert json.loads(invoke("files", "geo:GSE5", "--file", "none.*").stdout)["files"] == []


def test_files_acquisition_is_the_kernel_request(catalog, monkeypatch):
    monkeypatch.setattr(fake_adapter, "FILES", [A, B, C])
    out = invoke("files", "geo:GSE5", "--acquisition")
    assert out.exit_code == 1 and out.stdout == "" and json.loads(out.stderr)["error"]["kind"] == "unsupported_transport"
    out = invoke("files", "geo:GSE5", "--acquisition", "--file", "a.txt", "--file", "c.csv")
    assert out.exit_code == 0, out.stderr
    assert json.loads(out.stdout) == {
        "title": "shown", "locator": "accession:geo:GSE5",
        "resources": [{"name": "a.txt", "url": "https://alpha.example/a.txt", "expected": "sha256:" + "ab" * 32},
                      {"name": "c.csv", "url": "https://alpha.example/c.csv"}]}
    out = invoke("files", "geo:GSE5", "--acquisition", "--file", "none.*")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "no_files"


@pytest.mark.parametrize("curie, kind", [("doi:10.1000/x", "no_owner"), ("go:0008150", "unsupported_role"), ("GSE5", "ambiguous_id")])
def test_files_refusals_match_show(catalog, curie, kind):
    out = invoke("files", curie)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == kind


def test_files_pretty_output(catalog, monkeypatch):
    monkeypatch.setattr(fake_adapter, "FILES", [A, B])
    out = CliRunner().invoke(main, ["files", "geo:GSE5"], catch_exceptions=False)
    assert out.stdout == "a.txt\t3\thttps://alpha.example/a.txt\nb.bin\t-\tftp://alpha.example/b.bin\n# 2 files\n"
    out = CliRunner().invoke(main, ["files", "geo:GSE5", "--acquisition", "--file", "a.txt"], catch_exceptions=False)
    assert json.loads(out.stdout)["locator"] == "accession:geo:GSE5"
```

Add to `tests/fake_adapter.py`, after the imports and before `class Fake`:

```python
from quarry.files import File

FILES: list[File] = []          # what Fake.files lists; a test sets it
```

and inside `class Fake`:

```python
    def files(self, client, curie):
        return self.show(client, curie), list(FILES)
```

Run: `just test-one tests/test_files.py`
Expected: the four new tests FAIL with `No such command 'files'` (exit 2).

- [ ] **Step 7: Adopt the table and write the command**

```bash
(cd "$TREE/ops" && python3 bin/vendored adopt --into "$WT" cli.toml)
grep -n 'path = \["files"\]' tools/cli.toml
```

Expected: one match. (`tests/test_cli_surface.py::test_surface_equals_table` now fails until the command exists.)

In `src/quarry/adapters/base.py`, add to the `Adapter` protocol, after `show`:

```python
    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        """The record and its files, every location the resource gives; not_found when missing."""
        ...
```

with `from quarry.files import File` among the imports.

In `src/quarry/cli.py`, add `import json` to the imports, plus
`from quarry import acquisition` and `from quarry import files as files_mod`, and
`from quarry.catalog import DATA_ROLES, ROLES, ROUTE_KINDS, CatalogError, Finding, Resource`.
Replace the body of `show_command` from `parsed = ...` through the `unsupported_role` raise with
a call to the shared lookup, and add the lookup and the new command:

```python
def _owned(env: context.Env, text: str) -> tuple[idmod.Curie, Resource]:
    """The CURIE and the data-holding resource that owns its prefix (spec §7)."""
    parsed = idmod.parse(text, env.registry)
    owner = env.catalog.owner(parsed.prefix)
    if owner is None:
        raise QuarryError("no_owner", f"no catalog resource owns {parsed.prefix}: ids under it are cross-references only")
    if not set(owner.roles) & DATA_ROLES:
        raise QuarryError("unsupported_role", f"{owner.id} holds no records")
    return parsed, owner


@main.command("show", context_settings=CONTEXT)
@output_options
@click.argument("curie")
@click.pass_context
@reports_errors
def show_command(ctx: click.Context, curie: str) -> None:
    """Show one record."""
    env = context.open_env()
    parsed, owner = _owned(env, curie)
    with context.make_client(state(ctx).trace) as client:
        result = adapter_base.make(owner).show(client, parsed)
    emit(ctx, result.as_dict(), f"{result.id}\t{result.date or '-'}\t{result.title}")


def _pretty_files(listed: list[files_mod.File]) -> str:
    lines = [f"{f.name}\t{f.size if f.size is not None else '-'}\t{f.urls[0] if f.urls else '-'}" for f in listed]
    return "\n".join([*lines, f"# {len(listed)} files"])


@main.command("files", context_settings=CONTEXT)
@output_options
@click.argument("curie")
@click.option("--file", "globs", multiple=True, help="Only files whose name matches this glob; repeatable, widening.")
@click.option("--acquisition", "as_request", is_flag=True, help="Print the acquisition request the kernel takes (spec §9).")
@click.pass_context
@reports_errors
def files_command(ctx: click.Context, curie: str, globs: tuple[str, ...], as_request: bool) -> None:
    """List a record's files, or the acquisition request the kernel takes for them."""
    env = context.open_env()
    parsed, owner = _owned(env, curie)
    with context.make_client(state(ctx).trace) as client:
        record, listed = adapter_base.make(owner).files(client, parsed)
    listed = files_mod.select(listed, globs)
    if as_request:
        request = acquisition.build(record, parsed, listed)
        emit(ctx, request, json.dumps(request, indent=1))
        return
    emit(ctx, {"id": str(parsed), "resource": owner.id, "files": [f.as_dict() for f in listed]}, _pretty_files(listed))
```

Run: `just test-one tests/test_files.py tests/test_search.py tests/test_cli_surface.py::test_surface_equals_table`
Expected: PASS.

- [ ] **Step 8: Write the failing ENA `files` tests**

Append to `tests/test_ena.py`:

```python
FILE_ROW = {"run_accession": "SRR1",
            "fastq_ftp": "ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_1.fastq.gz;ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_2.fastq.gz",
            "fastq_md5": "a1;a2", "fastq_bytes": "10;20", "submitted_ftp": "", "submitted_md5": "", "submitted_bytes": "",
            "sra_ftp": "", "sra_md5": "", "sra_bytes": ""}


def filereport(accession, body):
    return exchange("GET", f"{BASE}/filereport", {"accession": accession, "result": "read_run", "fields": ena.FILE_FIELDS,
                                                 "format": "json", "limit": 0}, body=body)


def shown(field, local, body=json.dumps([STUDY])):
    return exchange("GET", f"{BASE}/search", {"result": "study", "query": f'{field}="{local}"', "fields": ena.FIELDS,
                                             "format": "json", "limit": 2}, body=body)


def test_files_split_paths_md5s_and_sizes_and_serve_https_first():
    transport = ReplayTransport([shown("study_accession", "PRJNA1"), filereport("PRJNA1", json.dumps([FILE_ROW]))])
    with replay_client(transport) as client:
        record, files = ENA.files(client, Curie("bioproject", "PRJNA1"))
    assert record.id == "bioproject:PRJNA1"
    assert [f.as_dict() for f in files] == [
        {"name": "SRR1_1.fastq.gz", "size": 10, "checksums": {"md5": "a1"},
         "urls": ["https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_1.fastq.gz", "ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_1.fastq.gz"]},
        {"name": "SRR1_2.fastq.gz", "size": 20, "checksums": {"md5": "a2"},
         "urls": ["https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_2.fastq.gz", "ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR1/SRR1_2.fastq.gz"]}]


def test_files_of_a_study_without_runs_is_empty_and_a_missing_study_is_not_found():
    with replay_client(ReplayTransport([shown("study_accession", "PRJNA1"), filereport("PRJNA1", "[\n\n]")])) as client:
        assert ENA.files(client, Curie("bioproject", "PRJNA1"))[1] == []
    with replay_client(ReplayTransport([shown("study_accession", "PRJNA9", body="")])) as client, \
            pytest.raises(QuarryError) as caught:
        ENA.files(client, Curie("bioproject", "PRJNA9"))
    assert caught.value.kind == "not_found"


@pytest.mark.parametrize("row", [
    {**FILE_ROW, "fastq_md5": "a1"},                                                          # two paths, one md5
    {**FILE_ROW, "fastq_bytes": "10;x"},                                                      # a size that is not a number
    {**FILE_ROW, "fastq_ftp": "example.org/evil.gz", "fastq_md5": "a", "fastq_bytes": "1"},  # off the ENA host
    {**FILE_ROW, "run_accession": 7},
    {**FILE_ROW, "fastq_ftp": ["x"]},
])
def test_malformed_filereport_rows_are_upstream_errors(row):
    transport = ReplayTransport([shown("study_accession", "PRJNA1"), filereport("PRJNA1", json.dumps([row]))])
    with replay_client(transport) as client, pytest.raises(QuarryError) as caught:
        ENA.files(client, Curie("bioproject", "PRJNA1"))
    assert caught.value.kind == "upstream_failed"


def test_recorded_files_and_acquisition(monkeypatch):
    """Mirrors tests/recordings/ena.py::files_first: the first myeloma study with read files."""
    with replay("files-first") as client:
        _, rows = ENA.search(client, MYELOMA)
        for row in rows:
            prefix, local = row.id.split(":", 1)
            record, files = ENA.files(client, Curie(prefix, local))
            if files:
                break
    assert files, "the files-first recording holds no study with files; re-record it"
    assert record.id == row.id and all(f.urls[0].startswith("https://ftp.sra.ebi.ac.uk/") for f in files)
    assert all("md5" in f.checksums and "sha256" not in f.checksums for f in files)
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-first"))
    out = CliRunner().invoke(main, ["--json", "files", row.id, "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0, out.stderr
    request = json.loads(out.stdout)
    assert request["locator"] == f"accession:{row.id}" and request["title"] == record.title
    assert len(request["resources"]) == len(files)
    assert all("expected" not in r and r["url"].startswith("https://") for r in request["resources"])
```

Run: `just test-one tests/test_ena.py -k files`
Expected: FAIL (`Ena` has no attribute `files`; no `FILE_FIELDS`).

- [ ] **Step 9: Implement ENA `files` and the recording scenario**

In `src/quarry/adapters/ena.py`, add `from quarry.files import File` and:

```python
FILE_FIELDS = ("run_accession,fastq_ftp,fastq_md5,fastq_bytes,submitted_ftp,submitted_md5,submitted_bytes,"
               "sra_ftp,sra_md5,sra_bytes")
FTP_HOST = "ftp.sra.ebi.ac.uk/"


def _split(d: dict, key: str) -> list[str]:
    value = d.get(key)
    if value is None or value == "":
        return []
    if type(value) is not str:
        raise UpstreamError(f"ena filereport: {key} is not text")
    return value.split(";")


def _files(rows: list[dict]) -> list[File]:
    """One File per path in the run's fastq, submitted and sra columns; md5 and bytes align by index."""
    files = []
    for d in rows:
        if type(d.get("run_accession")) is not str:
            raise UpstreamError("ena filereport: run_accession is not text")
        for kind in ("fastq", "submitted", "sra"):
            paths, md5s, sizes = (_split(d, f"{kind}_{suffix}") for suffix in ("ftp", "md5", "bytes"))
            if not paths:
                continue
            if len(md5s) not in (0, len(paths)) or len(sizes) not in (0, len(paths)):
                raise UpstreamError(f"ena filereport: {kind} paths, md5s and sizes disagree for {d['run_accession']}")
            for i, path in enumerate(paths):
                if not path.startswith(FTP_HOST):
                    raise UpstreamError("ena filereport: a file is not under ftp.sra.ebi.ac.uk")
                if sizes and not re.fullmatch(r"[0-9]+", sizes[i]):
                    raise UpstreamError("ena filereport: a size is not a number")
                files.append(File(name=path.rsplit("/", 1)[-1], urls=(f"https://{path}", f"ftp://{path}"),
                                  size=int(sizes[i]) if sizes else None, checksums={"md5": md5s[i]} if md5s else {}))
    return files
```

and in `class Ena`:

```python
    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record = self.show(client, curie)             # not_found before the file report
        listing = client.request(self.route, "GET", "filereport", params={
            "accession": curie.local, "result": "read_run", "fields": FILE_FIELDS, "format": "json", "limit": 0}).text
        return record, _files(_rows(listing))
```

In `tests/recordings/ena.py`, add:

```python
def files_first(adapter, client):
    """The first myeloma study that has read files; tests/test_ena.py mirrors this walk."""
    _, rows = adapter.search(client, MYELOMA)
    for row in rows:
        prefix, local = row.id.split(":", 1)
        if adapter.files(client, Curie(prefix, local))[1]:
            return
    raise ValueError("no ENA myeloma study has read files; widen MYELOMA dates before recording files-first")
```

and `"files-first": files_first` in `SCENARIOS`.

- [ ] **Step 10: Record and replay**

```bash
just record ena
ls -la tests/fixtures/ena/
```

Expected: `files-first.json` exists beside refreshed `search-myeloma.json`, `search-hyphen.json`,
`show-first.json`; the recorder prints one `recorded ena/<name>: N exchanges` line per scenario.
If `files-first.json` is larger than about 1 MB, the first study with files is a large
project: set `MYELOMA`'s window to a different quarter and record again.

Run: `just test-one tests/test_ena.py`
Expected: PASS.

- [ ] **Step 11: Give the conformance leaf test a `files` invocation**

In `tests/test_cli_surface.py::test_json_is_honoured_by_every_leaf_command`, add
`fx / "files-first.json"` to the `ReplayTransport.from_files([...])` list and
`("files",): ["files", f"bioproject:{shown}"],` to `invocations` (the files-first recording
starts at the same first study `show-first` shows, so its show and file report are present).

Run: `just test-fast`
Expected: PASS, every test.

- [ ] **Step 12: Commit**

```bash
git add src/quarry tests tools/cli.toml
git commit -m "feat(files): list a record's files and emit the kernel's acquisition request"
```

### Task 3: The E-utilities helper and GEO

**Files:**
- Create: `src/quarry/adapters/eutils.py`, `src/quarry/adapters/geo.py`, `src/quarry/catalog/resources/geo.toml`, `tests/test_eutils.py`, `tests/test_geo.py`, `tests/recordings/geo.py`, `tests/fixtures/geo/*.json` (recorded)
- Modify: `src/quarry/catalog.py` (`NOT_ADAPTERS` gains `eutils`), `src/quarry/catalog/domains/biology.toml` (`resources` gains `geo`; `search_default` waits for Task 8)

**Interfaces:**
- Consumes: Task 2's `File`, `Adapter.files`, `Coverage.warnings`; `Client.request`, `completeness`, `Query`, `Result`, `Curie`, `QuarryError`, `UpstreamError`.
- Produces, for Task 4: `quarry.adapters.eutils` with `WINDOW = 10_000`, `BATCH = 500`, `phrase(text) -> str`, `phrases(query) -> str`, `dates(query) -> str | None`, `iso_date(text) -> str | None`, `esearch(client, route, db, term, limit) -> tuple[int, list[str]]`, `esummary(client, route, db, ids) -> list[dict]`, `esummary_one(client, route, db, uid) -> dict | None`. For Task 8: resource `geo` owning prefix `geo`; `quarry.adapters.geo.compile_query`, class `Geo` (`match = "exact"`, `filters = frozenset({"organism"})`), `make`.

- [ ] **Step 1: Write the resource file and list GEO in biology**

`src/quarry/catalog/resources/geo.toml`:

```toml
id = "geo"
name = "Gene Expression Omnibus"
roles = ["repository"]
prefixes = ["geo"]
adapter = "quarry.adapters.geo"

[[routes]]                             # E-utilities: search and summaries; PubMed shares this service
kind = "rest"
url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
min_interval = 0.34
service = "ncbi-eutils"
auth = "optional-key"
key_env = "NCBI_API_KEY"
key_param = "api_key"
keyed_min_interval = 0.1
reports_total = true
page_cap = 10000

[[routes]]                             # the accession display: brief SOFT text with files and relations
kind = "rest"
url = "https://www.ncbi.nlm.nih.gov/geo/query"
min_interval = 0.34
service = "ncbi-geo"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "esearch serves at most 10,000 ids per query without the history server"
handling = "a count above the window is refused with too_many_results before any summary request; -n inside the window is served"
evidence = "E-utilities documentation (retstart + retmax at most 10,000); mm-159cb7 capped retmax by hand"

[[pitfalls]]
summary = "a SuperSeries repeats its SubSeries' samples, so a naive harvest counts them twice"
handling = "show reads the brief SOFT text and lists SuperSeries/SubSeries relations as geo: cross-references; GEO's sentence stays in the summary"
evidence = "mm-159cb7: resolving membership from brief text brought a 1.5-6 h harvest to about 10 min"

[[pitfalls]]
summary = "suppl/filelist.txt is missing for many series and no checksum is published"
handling = "files reads !Series_supplementary_file lines from the brief text and serves the HTTPS mirror of each FTP path; checksums stay unset"
evidence = "probe 2026-10-10: GSE24080 has filelist.txt, GSE193531 answers 404; the brief text lists both series' files"

[[pitfalls]]
summary = "a series names several papers"
handling = "the first PubMed id is the publication, the rest are xrefs, and the search report warns"
evidence = "GSE24080 lists three PubMed ids"
```

In `src/quarry/catalog/domains/biology.toml`: `resources = ["geo", "ena"]` (leave `search_default = ["ena"]`).
In `src/quarry/catalog.py`: `NOT_ADAPTERS = frozenset({"base", "eutils"})`.

- [ ] **Step 2: Write the failing helper tests**

`tests/test_eutils.py`:

```python
"""The shared E-utilities helper: phrases, dates, the 10,000-id window, batched summaries."""
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

from quarry import catalog as catalog_mod
from quarry.adapters import eutils
from quarry.errors import QuarryError
from quarry.query import Query
from quarry.testing import replay_client

ROUTE = catalog_mod.load().resources["geo"].routes[0]


def test_phrases_are_quoted_all_fields_or_clauses_and_refuse_quotes():
    assert eutils.phrases(Query(phrases=("multiple myeloma", "MGUS"))) == \
        '("multiple myeloma"[All Fields] OR "MGUS"[All Fields])'
    with pytest.raises(QuarryError) as caught:
        eutils.phrases(Query(phrases=('the "MM" cohort',)))
    assert caught.value.kind == "unsupported_filter"


def test_dates_are_pdat_ranges_with_open_ends():
    assert eutils.dates(Query(phrases=("x",))) is None
    assert eutils.dates(Query(phrases=("x",), since=date(2024, 1, 1), until=date(2024, 3, 31))) == \
        '("2024/01/01"[PDAT] : "2024/03/31"[PDAT])'
    assert eutils.dates(Query(phrases=("x",), since=date(2024, 1, 1))) == '("2024/01/01"[PDAT] : "3000/12/31"[PDAT])'
    assert eutils.dates(Query(phrases=("x",), until=date(2024, 3, 31))) == '("1800/01/01"[PDAT] : "2024/03/31"[PDAT])'


@pytest.mark.parametrize("text, expected", [("2010/09/11", "2010-09-11"), ("2024/02/01 00:00", "2024-02-01"), ("", None), (None, None)])
def test_iso_date(text, expected):
    assert eutils.iso_date(text) == expected


@pytest.mark.parametrize("text", ["2010-09-11", "Sep 2010", 7])
def test_iso_date_refuses_other_shapes(text):
    with pytest.raises(QuarryError) as caught:
        eutils.iso_date(text)
    assert caught.value.kind == "upstream_failed"


def service(count, uids, summaries=None):
    """A fake E-utilities: esearch pages `uids` by retstart/retmax; esummary answers from `summaries`."""
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path.endswith("esearch.fcgi"):
            start, retmax = int(request.url.params["retstart"]), int(request.url.params["retmax"])
            return httpx.Response(200, json={"esearchresult": {"count": str(count), "idlist": uids[start:start + retmax]}})
        form = parse_qs(request.content.decode()) if request.content else {k: [v] for k, v in request.url.params.items()}
        ids = form["id"][0].split(",")
        return httpx.Response(200, json={"result": {"uids": ids, **{u: (summaries or {}).get(u, {"uid": u}) for u in ids}}})

    return seen, replay_client(httpx.MockTransport(handle))


def test_esearch_returns_every_id_in_one_window():
    seen, client = service(3, ["1", "2", "3"])
    assert eutils.esearch(client, ROUTE, "gds", "t", None) == (3, ["1", "2", "3"])
    assert len(seen) == 1 and seen[0].url.params["retmax"] == "10000" and seen[0].url.params["retstart"] == "0"


def test_esearch_limit_asks_for_the_limit_and_stops_there():
    seen, client = service(50, [str(i) for i in range(50)])
    assert eutils.esearch(client, ROUTE, "gds", "t", 5) == (50, ["0", "1", "2", "3", "4"])
    assert seen[0].url.params["retmax"] == "5"


def test_esearch_pages_by_retstart_when_a_page_comes_back_short():
    uids = [str(i) for i in range(30)]
    seen = []

    def handle(request):
        seen.append(request)
        start = int(request.url.params["retstart"])
        return httpx.Response(200, json={"esearchresult": {"count": "30", "idlist": uids[start:start + 20]}})
    client = replay_client(httpx.MockTransport(handle))
    assert eutils.esearch(client, ROUTE, "gds", "t", None) == (30, uids)
    assert [r.url.params["retstart"] for r in seen] == ["0", "20"]


def test_esearch_refuses_a_count_beyond_the_window_before_summaries():         # Review Focus 5
    seen, client = service(10_001, [])
    with pytest.raises(QuarryError) as caught:
        eutils.esearch(client, ROUTE, "gds", "t", None)
    assert caught.value.kind == "too_many_results" and len(seen) == 1
    with pytest.raises(QuarryError) as caught:
        eutils.esearch(client, ROUTE, "gds", "t", 10_001)
    assert caught.value.kind == "too_many_results"
    seen, client = service(10_001, [str(i) for i in range(50)])
    assert eutils.esearch(client, ROUTE, "gds", "t", 50)[1] == [str(i) for i in range(50)]


def test_esummary_posts_in_batches_in_order_and_refuses_a_missing_id():
    ids = [str(i) for i in range(1001)]
    seen, client = service(0, [], {u: {"uid": u, "title": f"t{u}"} for u in ids})
    records = eutils.esummary(client, ROUTE, "gds", ids)
    assert [r["title"] for r in records] == [f"t{u}" for u in ids]
    assert len(seen) == 3 and all(r.method == "POST" for r in seen)
    seen, client = service(0, [], {"1": {"uid": "1", "error": "cannot get document summary"}})
    with pytest.raises(QuarryError) as caught:
        eutils.esummary(client, ROUTE, "gds", ["1"])
    assert caught.value.kind == "upstream_failed"


def test_esummary_one_is_none_for_an_unknown_uid():
    _, client = service(0, [], {"1": {"uid": "1", "error": "cannot get document summary"}, "2": {"uid": "2", "title": "x"}})
    assert eutils.esummary_one(client, ROUTE, "pubmed", "1") is None
    assert eutils.esummary_one(client, ROUTE, "pubmed", "2") == {"uid": "2", "title": "x"}


@pytest.mark.parametrize("body", ["<html>busy</html>", '{"error": "x"}', '{"esearchresult": {"count": "x", "idlist": []}}',
                                  '{"esearchresult": {"count": "1", "idlist": [7]}}', '{"esearchresult": []}'])
def test_malformed_esearch_is_an_upstream_error(body):
    client = replay_client(httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
    with pytest.raises(QuarryError) as caught:
        eutils.esearch(client, ROUTE, "gds", "t", None)
    assert caught.value.kind == "upstream_failed"
```

Run: `just test-one tests/test_eutils.py`
Expected: FAIL at import (`cannot import name 'eutils'`), after the catalog loads `geo.toml`.

- [ ] **Step 3: Write the helper**

`src/quarry/adapters/eutils.py`:

```python
"""NCBI E-utilities, shared by GEO (db=gds) and PubMed (db=pubmed): quoted [All Fields] phrases,
[PDAT] bounds, esearch paged by retstart inside the 10,000-id window, esummary in POSTed
batches. Both resources' routes name service ncbi-eutils, so they share one throttle and key."""
from __future__ import annotations

import json
import re
from datetime import date

from quarry.catalog import Route
from quarry.errors import QuarryError
from quarry.http import Client, UpstreamError
from quarry.query import Query

WINDOW = 10_000            # retstart + retmax never exceeds this without the history server
BATCH = 500                # esummary ids per POST
UID = re.compile(r"^[0-9]+$")
SLASH_DATE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})")


def phrase(text: str) -> str:
    """A literal phrase: Entrez reads a double quote as syntax and documents no escape."""
    if '"' in text:
        raise QuarryError("unsupported_filter", f"an E-utilities phrase cannot contain a double quote: {text!r}; use --native")
    return f'"{text}"[All Fields]'


def phrases(query: Query) -> str:
    return "(" + " OR ".join(phrase(p) for p in query.phrases) + ")"


def _pdat(when: date | None, open_end: str) -> str:
    return f'"{when.strftime("%Y/%m/%d")}"[PDAT]' if when else f'"{open_end}"[PDAT]'


def dates(query: Query) -> str | None:
    if query.since is None and query.until is None:
        return None
    return f"({_pdat(query.since, '1800/01/01')} : {_pdat(query.until, '3000/12/31')})"


def iso_date(text: object) -> str | None:
    """'2010/09/11' or '2024/02/01 00:00' as ISO; None when absent; an upstream fault otherwise."""
    if text is None or text == "":
        return None
    match = SLASH_DATE.match(text) if type(text) is str else None
    if match is None:
        raise UpstreamError("eutils: a date is not YYYY/MM/DD")
    return "-".join(match.groups())


def _object(text: str, key: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"eutils {key}: not JSON") from None
    if type(data) is not dict or "error" in data or type(data.get(key)) is not dict:
        raise UpstreamError(f"eutils {key}: expected a JSON object holding {key!r}")
    return data[key]


def esearch(client: Client, route: Route, db: str, term: str, limit: int | None) -> tuple[int, list[str]]:
    """(count, ids): every matching id, or the first `limit`, paged by retstart. A count the window
    cannot hold is refused before anything else is asked (spec §8.3: no silent truncation)."""
    ids: list[str] = []
    wanted = WINDOW if limit is None else min(limit, WINDOW)
    while True:
        result = _object(client.request(route, "GET", "esearch.fcgi", params={
            "db": db, "term": term, "retmode": "json", "retstart": len(ids), "retmax": wanted - len(ids)}).text,
            "esearchresult")
        page = result.get("idlist")
        if (type(page) is not list or any(type(i) is not str or not UID.match(i) for i in page)
                or type(result.get("count")) is not str or not UID.match(result["count"])):
            raise UpstreamError("eutils esearch: malformed count or idlist")
        count = int(result["count"])
        if count > WINDOW and (limit is None or limit > WINDOW):
            raise QuarryError("too_many_results", f"{count} records match, more than the {WINDOW} E-utilities serves "
                                                  f"per query; narrow the query or pass -n")
        ids += page
        if not page or len(ids) >= min(count, wanted):
            return count, ids


def esummary(client: Client, route: Route, db: str, ids: list[str]) -> list[dict]:
    """One summary per id, in the order given, POSTed in batches; an id esearch just listed must answer."""
    records = []
    for start in range(0, len(ids), BATCH):
        batch = ids[start:start + BATCH]
        result = _object(client.request(route, "POST", "esummary.fcgi",
                                        data={"db": db, "retmode": "json", "id": ",".join(batch)}).text, "result")
        for uid in batch:
            record = result.get(uid)
            if type(record) is not dict or "error" in record:
                raise UpstreamError("eutils esummary: no summary for an id esearch listed")
            records.append(record)
    return records


def esummary_one(client: Client, route: Route, db: str, uid: str) -> dict | None:
    """The summary for one known uid, or None when E-utilities answers its error object for it."""
    result = _object(client.request(route, "GET", "esummary.fcgi", params={"db": db, "retmode": "json", "id": uid}).text,
                     "result")
    record = result.get(uid)
    if type(record) is not dict:
        raise UpstreamError("eutils esummary: malformed result")
    return None if "error" in record else record
```

Run: `just test-one tests/test_eutils.py`
Expected: PASS.

- [ ] **Step 4: Write the failing GEO tests**

`tests/test_geo.py`:

```python
"""GEO: compilation, synthetic and recorded searches, show with relations, files from the brief
text, and the credential sentinel through trace, recording and errors (spec §9, §12, §13)."""
import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import geo
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.http import Client, Throttle
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import RecordingTransport, ReplayTransport, VirtualClock, exchange, replay_client, secret_params
from tests.recordings.geo import GSE24080, MYELOMA

FX = Path(__file__).parent / "fixtures" / "geo"
CAT = catalog_mod.load()
GEO = geo.make(CAT.resources["geo"])
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
WEB = "https://www.ncbi.nlm.nih.gov/geo/query"
SUMMARY = {"uid": "200024080", "accession": "GSE24080", "title": "MAQC-II", "summary": "s", "taxon": "Homo sapiens",
           "entrytype": "GSE", "pdat": "2010/09/11", "pubmedids": ["20064235", "20676074"], "bioproject": "PRJNA130207",
           "extrelations": [{"relationtype": "SRA", "targetobject": "SRP000001", "targetftplink": ""}]}
BRIEF = ("!Series_title = MAQC-II\n!Series_geo_accession = GSE24080\n"
         "!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_clean.xls.gz\n"
         "!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_RAW.tar\n"
         "!Series_platform_taxid = 9606\n!Series_sample_taxid = 9606\n"
         "!Series_relation = SubSeries of: GSE16716\n"
         "!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA130207\n")


def test_compile_phrases_organism_and_dates():
    from datetime import date
    q = Query(phrases=("multiple myeloma", "MGUS"), filters={"organism": "ncbitaxon:9606"},
              since=date(2024, 1, 1), until=date(2024, 1, 31))
    assert geo.compile_query(q) == ('("multiple myeloma"[All Fields] OR "MGUS"[All Fields]) AND gse[ETYP] '
                                    'AND txid9606[ORGN] AND ("2024/01/01"[PDAT] : "2024/01/31"[PDAT])')
    assert geo.compile_query(Query(phrases=("x",), filters={"organism": "Homo sapiens"})).endswith('AND "Homo sapiens"[ORGN]')
    assert geo.compile_query(Query(native="GSE1[ACCN]")) == "GSE1[ACCN]"


@pytest.mark.parametrize("query", [Query(phrases=('a "b"',)), Query(phrases=("x",), filters={"organism": 'Homo "sapiens"'})])
def test_quotes_are_refused_before_any_request(query):
    with pytest.raises(QuarryError) as caught:
        GEO.prepare(query)
    assert caught.value.kind == "unsupported_filter"


def esearch(term, count, ids, retmax=10000):
    return exchange("GET", f"{EU}/esearch.fcgi", {"db": "gds", "term": term, "retmode": "json", "retstart": 0, "retmax": retmax},
                    body=json.dumps({"esearchresult": {"count": str(count), "idlist": ids}}))


def esummary(records):
    return exchange("POST", f"{EU}/esummary.fcgi", form={"db": "gds", "retmode": "json", "id": ",".join(r["uid"] for r in records)},
                    body=json.dumps({"result": {"uids": [r["uid"] for r in records], **{r["uid"]: r for r in records}}}))


def brief(acc, text):
    return exchange("GET", f"{WEB}/acc.cgi", {"acc": acc, "targ": "self", "form": "text", "view": "brief"}, body=text,
                    content_type="geo/text")


def test_synthetic_search_rows_publication_xrefs_and_warning():
    q = geo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([esearch(q, 1, ["200024080"]), esummary([SUMMARY])]))
    cov, rows = GEO.search(client, Query(phrases=("x",)))
    assert cov.complete == "total" and cov.total == 1 and cov.match == "exact"
    row = rows[0]
    assert row.id == "geo:GSE24080" and row.date == "2010-09-11" and row.kind == "dataset"
    assert row.publication == "pubmed:20064235"
    assert row.xrefs == ("bioproject:PRJNA130207", "insdc.sra:SRP000001", "pubmed:20676074")
    assert row.domain_fields == {"biology": {"organism": "Homo sapiens"}}
    assert cov.warnings == ("geo:GSE24080 names 2 papers; pubmed:20064235 is its publication and the rest are xrefs",)


def test_synthetic_limit_is_limited():
    query = Query(phrases=("x",), limit=1)
    q = geo.compile_query(query)
    client = replay_client(ReplayTransport([esearch(q, 5, ["200024080"], retmax=1), esummary([SUMMARY])]))
    cov, rows = GEO.search(client, query)
    assert cov.complete == "limited" and len(rows) == 1


@pytest.mark.parametrize("field, value", [("accession", "GSM1"), ("accession", None), ("title", 7), ("pdat", "Sep 2010"),
                                          ("pubmedids", "20064235"), ("pubmedids", ["x"]), ("extrelations", "SRA")])
def test_malformed_summaries_are_upstream_errors(field, value):
    q = geo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([esearch(q, 1, ["200024080"]), esummary([{**SUMMARY, field: value}])]))
    with pytest.raises(QuarryError) as caught:
        GEO.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


def fetch(summary=SUMMARY, text=BRIEF):
    return ReplayTransport([esearch("GSE24080[ACCN] AND gse[ETYP]", 1, ["200024080"], retmax=1), esummary([summary]),
                            brief("GSE24080", text)])


def test_show_adds_relations_and_taxon_from_the_brief():
    shown = GEO.show(replay_client(fetch()), Curie("geo", "GSE24080"))
    assert shown.xrefs == ("bioproject:PRJNA130207", "insdc.sra:SRP000001", "pubmed:20676074", "geo:GSE16716")
    assert shown.domain_fields == {"biology": {"organism": "Homo sapiens", "taxon": "ncbitaxon:9606"}}


def test_files_come_from_the_brief_with_https_first_and_no_checksum():
    record, files = GEO.files(replay_client(fetch()), Curie("geo", "GSE24080"))
    assert record.id == "geo:GSE24080"
    assert [f.as_dict() for f in files] == [
        {"name": "GSE24080_clean.xls.gz", "urls": ["https://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_clean.xls.gz",
                                                   "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_clean.xls.gz"]},
        {"name": "GSE24080_RAW.tar", "urls": ["https://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_RAW.tar",
                                              "ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_RAW.tar"]}]


def test_series_without_supplementary_files_lists_nothing_and_cannot_be_acquired(monkeypatch):   # Review Focus 2
    text = "\n".join(line for line in BRIEF.splitlines() if "supplementary" not in line) + "\n"
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(fetch(text=text)))
    out = CliRunner().invoke(main, ["--json", "files", "geo:GSE24080"], catch_exceptions=False)
    assert out.exit_code == 0 and json.loads(out.stdout)["files"] == []
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(fetch(text=text)))
    out = CliRunner().invoke(main, ["--json", "files", "geo:GSE24080", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "no_files"


def test_missing_series_is_not_found_and_other_geo_ids_are_refused_first():
    client = replay_client(ReplayTransport([esearch("GSE9[ACCN] AND gse[ETYP]", 0, [], retmax=1)]))
    with pytest.raises(QuarryError) as caught:
        GEO.show(client, Curie("geo", "GSE9"))
    assert caught.value.kind == "not_found"
    seen = []
    client = replay_client(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    with pytest.raises(QuarryError) as caught:
        GEO.show(client, Curie("geo", "GSM1"))
    assert caught.value.kind == "unsupported_id" and seen == []


def test_brief_that_is_not_soft_is_an_upstream_error():
    with pytest.raises(QuarryError) as caught:
        GEO.show(replay_client(fetch(text="<html>no such series</html>")), Curie("geo", "GSE24080"))
    assert caught.value.kind == "upstream_failed"


def test_too_many_results_through_the_cli_before_any_summary(monkeypatch):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"esearchresult": {"count": "10001", "idlist": []}})
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(httpx.MockTransport(handle)))
    out = CliRunner().invoke(main, ["--json", "search", "--in", "geo", "x"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "too_many_results" and len(seen) == 1


def test_key_never_reaches_trace_fixture_or_error(tmp_path):
    sentinel = "GEO-SENTINEL+KEY/1="
    traced, calls = [], []

    def echo(request):
        calls.append(request)
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["200024080"]}})
        return httpx.Response(500, text=f"busy; your key {sentinel}")
    recorder = RecordingTransport(secret_params(CAT), [sentinel], inner=httpx.MockTransport(echo))
    client = Client(transport=recorder, throttle=Throttle(VirtualClock()), trace=traced.append, environ={"NCBI_API_KEY": sentinel})
    with pytest.raises(QuarryError) as caught:
        GEO.search(client, Query(phrases=("x",)))
    RecordingTransport.save(recorder.take(), tmp_path / "f.json")
    everything = "\n".join(traced) + caught.value.detail + (tmp_path / "f.json").read_text()
    assert sentinel not in everything and "REDACTED" in "\n".join(traced)
    assert all(c.url.params["api_key"] == sentinel for c in calls)


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_is_complete_human_and_canonical():
    cov, rows = GEO.search(replay("search-myeloma"), MYELOMA)
    assert cov.complete == "total" and cov.returned == cov.total == len(rows) > 0
    assert all(r.id.startswith("geo:GSE") and r.resource == "geo" for r in rows)
    assert all(r.domain_fields["biology"]["organism"] == "Homo sapiens" for r in rows)
    assert all(r.publication is None or r.publication.startswith("pubmed:") for r in rows)


def test_recorded_show_gse24080():
    shown = GEO.show(replay("show-gse24080"), GSE24080)
    assert shown.publication == "pubmed:20064235"
    assert {"pubmed:20676074", "pubmed:27363682", "bioproject:PRJNA130207", "geo:GSE16716"} <= set(shown.xrefs)
    assert shown.domain_fields["biology"] == {"organism": "Homo sapiens", "taxon": "ncbitaxon:9606"}


def test_recorded_files_and_acquisition(monkeypatch):
    _, files = GEO.files(replay("files-gse24080"), GSE24080)
    assert [f.name for f in files] == ["GSE24080_MM_UAMS565_ClinInfo_27Jun2008_LS_clean.xls.gz", "GSE24080_RAW.tar"]
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-gse24080"))
    out = CliRunner().invoke(main, ["--json", "files", "geo:GSE24080", "--acquisition"], catch_exceptions=False)
    request = json.loads(out.stdout)
    assert out.exit_code == 0 and request["locator"] == "accession:geo:GSE24080"
    assert [r["url"] for r in request["resources"]] == [
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_MM_UAMS565_ClinInfo_27Jun2008_LS_clean.xls.gz",
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE24nnn/GSE24080/suppl/GSE24080_RAW.tar"]
    assert all("expected" not in r for r in request["resources"])
```

`tests/recordings/geo.py`:

```python
"""The GEO exchanges tests/test_geo.py replays."""
from datetime import date

from quarry.ids import Curie
from quarry.query import Query

MYELOMA = Query(phrases=("multiple myeloma",), filters={"organism": "ncbitaxon:9606"},
                since=date(2024, 1, 1), until=date(2024, 1, 31))
GSE24080 = Curie("geo", "GSE24080")       # three papers, a SubSeries of GSE16716, two supplementary files

SCENARIOS = {
    "search-myeloma": lambda adapter, client: adapter.search(client, MYELOMA),
    "show-gse24080": lambda adapter, client: adapter.show(client, GSE24080),
    "files-gse24080": lambda adapter, client: adapter.files(client, GSE24080),
}
```

Run: `just test-one tests/test_geo.py`
Expected: FAIL at import (`cannot import name 'geo'`).

- [ ] **Step 5: Write the adapter**

`src/quarry/adapters/geo.py`:

```python
"""GEO through E-utilities db=gds (spec §13): phrases become quoted [All Fields] clauses over
series (gse[ETYP]); the brief SOFT text of the accession display supplies files, SuperSeries
relations and the sample taxon id."""
from __future__ import annotations

import re
from collections.abc import Callable

from quarry.adapters import eutils
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

TAXON = re.compile(r"^ncbitaxon:(\d+)$", re.IGNORECASE)
GSE = re.compile(r"^GSE\d+$")
PRJ = re.compile(r"^PRJ[DEN][A-Z]\d+$")
SRA = re.compile(r"^[SED]R[APRSXZ]\d+$")
FTP = "ftp://ftp.ncbi.nlm.nih.gov/"
SUPPL = re.compile(r"^!Series_supplementary_file = (\S+)$", re.M)
RELATION = re.compile(r"^!Series_relation = (?:SuperSeries of|SubSeries of): (GSE\d+)$", re.M)
TAXID = re.compile(r"^!Series_sample_taxid = ([0-9;]+)$", re.M)
STRINGS = ("title", "summary", "taxon", "pdat", "bioproject")


def _plain(text: str) -> str:
    if '"' in text:
        raise QuarryError("unsupported_filter", f"a GEO organism name cannot contain a double quote: {text!r}; use --native")
    return text


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    clauses = [eutils.phrases(query), "gse[ETYP]"]
    organism = query.filters.get("organism")
    if organism:
        taxon = TAXON.match(organism)
        clauses.append(f"txid{taxon.group(1)}[ORGN]" if taxon else f'"{_plain(organism)}"[ORGN]')
    when = eutils.dates(query)
    if when:
        clauses.append(when)
    return " AND ".join(clauses)


def _valid(d: dict) -> dict:
    if type(d.get("accession")) is not str or not GSE.match(d["accession"]):
        raise UpstreamError("geo esummary: accession is not a GSE series")
    for name in STRINGS:
        if d.get(name) is not None and type(d[name]) is not str:
            raise UpstreamError(f"geo esummary: {name} is not text")
    papers = d.get("pubmedids")
    if papers is not None and (type(papers) is not list or any(type(p) is not str or not p.isdigit() for p in papers)):
        raise UpstreamError("geo esummary: pubmedids is not a list of ids")
    relations = d.get("extrelations")
    if relations is not None and (type(relations) is not list or any(type(e) is not dict for e in relations)):
        raise UpstreamError("geo esummary: extrelations is not a list of relations")
    return d


def _row(d: dict, relations: tuple[str, ...] = (), taxon: str | None = None) -> tuple[Result, str | None]:
    """The result and, when the series names several papers, the warning the report carries."""
    d = _valid(d)
    acc = d["accession"]
    papers = list(d.get("pubmedids") or [])
    xrefs = [f"bioproject:{b}" for b in (d.get("bioproject") or "").split(";") if PRJ.match(b)]
    xrefs += [f"insdc.sra:{e['targetobject']}" for e in d.get("extrelations") or []
              if e.get("relationtype") == "SRA" and type(e.get("targetobject")) is str and SRA.match(e["targetobject"])]
    xrefs += [f"pubmed:{p}" for p in papers[1:]]
    xrefs += [r for r in relations if r not in xrefs]
    biology = {}
    if d.get("taxon"):
        biology["organism"] = d["taxon"]
    if taxon:
        biology["taxon"] = f"ncbitaxon:{taxon}"
    result = Result(id=f"geo:{acc}", resource="geo", kind="dataset", title=d.get("title") or "",
                    date=eutils.iso_date(d.get("pdat")), summary=d.get("summary") or None, xrefs=tuple(xrefs),
                    publication=f"pubmed:{papers[0]}" if papers else None,
                    domain_fields={"biology": biology} if biology else {})
    warning = (f"geo:{acc} names {len(papers)} papers; pubmed:{papers[0]} is its publication and the rest are xrefs"
               if len(papers) > 1 else None)
    return result, warning


class Geo:
    match = "exact"
    filters = frozenset({"organism"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.eutils, self.web = resource.routes[0], resource.routes[1]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            count, ids = eutils.esearch(client, self.eutils, "gds", q, query.limit)
            rows, warnings = [], []
            for d in eutils.esummary(client, self.eutils, "gds", ids):
                result, warning = _row(d)
                rows.append(result)
                if warning:
                    warnings.append(warning)
            complete = completeness(total=count, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, count, len(rows), complete, warnings=tuple(warnings)), rows

        return execute

    def _fetch(self, client: Client, curie: Curie) -> tuple[dict, str]:
        """The esummary record and the brief SOFT text of one series; refusals come first."""
        if not GSE.match(curie.local):
            raise QuarryError("unsupported_id", f"{curie}: quarry covers GEO series (GSE) only")
        _, ids = eutils.esearch(client, self.eutils, "gds", f"{curie.local}[ACCN] AND gse[ETYP]", 1)
        if not ids:
            raise QuarryError("not_found", f"{curie} is not a GEO series")
        summary = eutils.esummary(client, self.eutils, "gds", ids)[0]
        text = client.request(self.web, "GET", "acc.cgi",
                              params={"acc": curie.local, "targ": "self", "form": "text", "view": "brief"}).text
        if f"!Series_geo_accession = {curie.local}" not in text:
            raise UpstreamError("geo brief: not the series' SOFT text")
        return summary, text

    @staticmethod
    def _result(summary: dict, text: str) -> Result:
        relations = tuple(f"geo:{acc}" for acc in RELATION.findall(text))
        taxids = [t for found in TAXID.findall(text) for t in found.split(";") if t]
        return _row(summary, relations, taxids[0] if len(set(taxids)) == 1 else None)[0]

    def show(self, client: Client, curie: Curie) -> Result:
        return self._result(*self._fetch(client, curie))

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        summary, text = self._fetch(client, curie)
        files = []
        for url in SUPPL.findall(text):
            if not url.startswith(FTP):
                raise UpstreamError("geo brief: a supplementary file is not under ftp.ncbi.nlm.nih.gov")
            files.append(File(name=url.rsplit("/", 1)[1], urls=(f"https://ftp.ncbi.nlm.nih.gov/{url[len(FTP):]}", url)))
        return self._result(summary, text), files


def make(resource: Resource) -> Geo:
    return Geo(resource)
```

Run: `just test-one tests/test_geo.py -k "not recorded"`
Expected: PASS for every non-recorded test.

- [ ] **Step 6: Record and replay**

```bash
just record geo            # NCBI_API_KEY may be set or not; a set key is redacted in the fixtures
grep -c 'api_key' tests/fixtures/geo/*.json      # expect 0 lines holding the key value; REDACTED may appear
just test-one tests/test_geo.py
uv run quarry check
```

Expected: three fixture files; every test passes; `check` prints nothing (GEO's two routes,
its pitfalls and the `eutils` helper all pass the catalog check).

- [ ] **Step 7: Run the suite and commit**

```bash
just test-fast
git add src/quarry tests
git commit -m "feat(geo): series search, show with relations, files from the brief text"
```

### Task 4: PubMed, and the shared E-utilities allowance

**Files:**
- Create: `src/quarry/adapters/pubmed.py`, `src/quarry/catalog/resources/pubmed.toml`, `tests/test_pubmed.py`, `tests/recordings/pubmed.py`, `tests/fixtures/pubmed/*.json` (recorded)
- Modify: `src/quarry/catalog/domains/core.toml` (`resources` gains `pubmed`; `search_default` waits for Task 8)

**Interfaces:**
- Consumes: Task 3's `eutils` helper and the `geo` adapter (for the shared-throttle test); Task 2's protocol.
- Produces: resource `pubmed` (role `literature-index`, prefix `pubmed`); `quarry.adapters.pubmed.compile_query`, class `Pubmed` (`match = "expanded"`, `filters = frozenset()`), `make`. `files` on it is `unsupported_role`.

- [ ] **Step 1: Write the resource file and list PubMed in core**

`src/quarry/catalog/resources/pubmed.toml`:

```toml
id = "pubmed"
name = "PubMed"
roles = ["literature-index"]
prefixes = ["pubmed"]
adapter = "quarry.adapters.pubmed"

[[routes]]                             # the same E-utilities service as GEO: one throttle, one key
kind = "rest"
url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
min_interval = 0.34
service = "ncbi-eutils"
auth = "optional-key"
key_env = "NCBI_API_KEY"
key_param = "api_key"
keyed_min_interval = 0.1
reports_total = true
page_cap = 10000

[[pitfalls]]
summary = "PubMed maps phrases through automatic term mapping and, when a phrase is not indexed, searches its words ANDed"
handling = "coverage says match: expanded; --exact refuses PubMed; --native sends PubMed syntax unchanged"
evidence = "PubMed help: automatic term mapping and phrase searching"

[[pitfalls]]
summary = "esearch serves at most 10,000 ids per query without the history server"
handling = "a count above the window is refused with too_many_results before any summary request; -n inside the window is served"
evidence = "E-utilities documentation; mm-159cb7's reviews search capped retmax by hand"
```

In `src/quarry/catalog/domains/core.toml`: `resources = ["pubmed"]` (leave `search_default = []`).

PubMed now owns `pubmed`, so `tests/test_search.py::test_show_refusals` and
`tests/test_files.py::test_files_refusals_match_show` must use a prefix nothing owns for
their `no_owner` case: change `("pubmed:1", "no_owner")` to `("doi:10.1000/x", "no_owner")` in both
(spec §7: `doi:` has no owner in the first version).

- [ ] **Step 2: Write the failing tests**

`tests/test_pubmed.py`:

```python
"""PubMed: compilation, synthetic and recorded searches, show, no files, and the one E-utilities
allowance GEO and PubMed share (spec §9, §12, §13)."""
import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import geo, pubmed
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.http import Client, Throttle
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, VirtualClock, exchange, replay_client
from tests.recordings.pubmed import SMOLDERING

FX = Path(__file__).parent / "fixtures" / "pubmed"
CAT = catalog_mod.load()
PUBMED = pubmed.make(CAT.resources["pubmed"])
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SUMMARY = {"uid": "38195712", "title": "Inflammasomes.", "sortpubdate": "2024/02/01 00:00", "fulljournalname": "Nature reviews. Neurology",
           "articleids": [{"idtype": "pubmed", "value": "38195712"}, {"idtype": "doi", "value": "10.1038/s41582-023-00915-x"},
                          {"idtype": "pmc", "value": "PMC1234567"}]}


def test_compile_phrases_and_dates_without_organism():
    q = Query(phrases=("multiple myeloma", "MGUS"), since=date(2024, 1, 1))
    assert pubmed.compile_query(q) == ('("multiple myeloma"[All Fields] OR "MGUS"[All Fields]) '
                                       'AND ("2024/01/01"[PDAT] : "3000/12/31"[PDAT])')
    assert PUBMED.filters == frozenset() and PUBMED.match == "expanded"


def esearch(term, count, ids, retmax=10000):
    return exchange("GET", f"{EU}/esearch.fcgi", {"db": "pubmed", "term": term, "retmode": "json", "retstart": 0, "retmax": retmax},
                    body=json.dumps({"esearchresult": {"count": str(count), "idlist": ids}}))


def esummary(records):
    return exchange("POST", f"{EU}/esummary.fcgi", form={"db": "pubmed", "retmode": "json", "id": ",".join(r["uid"] for r in records)},
                    body=json.dumps({"result": {"uids": [r["uid"] for r in records], **{r["uid"]: r for r in records}}}))


def test_synthetic_search_rows_are_publications_with_doi_and_pmc_xrefs():
    q = pubmed.compile_query(Query(phrases=("x",)))
    cov, rows = PUBMED.search(replay_client(ReplayTransport([esearch(q, 1, ["38195712"]), esummary([SUMMARY])])), Query(phrases=("x",)))
    assert cov.complete == "total" and cov.match == "expanded"
    assert rows[0].as_dict() == {"id": "pubmed:38195712", "resource": "pubmed", "kind": "publication", "title": "Inflammasomes.",
                                 "date": "2024-02-01", "summary": "Nature reviews. Neurology",
                                 "xrefs": ["doi:10.1038/s41582-023-00915-x", "pmc:PMC1234567"], "domain_fields": {}}


@pytest.mark.parametrize("field, value", [("uid", 7), ("title", 7), ("sortpubdate", "Feb 2024"), ("articleids", "doi")])
def test_malformed_summaries_are_upstream_errors(field, value):
    q = pubmed.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([esearch(q, 1, ["38195712"]), esummary([{**SUMMARY, field: value}])]))
    with pytest.raises(QuarryError) as caught:
        PUBMED.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


def one(uid, body):
    return exchange("GET", f"{EU}/esummary.fcgi", {"db": "pubmed", "retmode": "json", "id": uid}, body=body)


def test_show_by_uid_and_not_found():
    client = replay_client(ReplayTransport([one("38195712", json.dumps({"result": {"uids": ["38195712"], "38195712": SUMMARY}}))]))
    assert PUBMED.show(client, Curie("pubmed", "38195712")).id == "pubmed:38195712"
    missing = json.dumps({"result": {"uids": ["9"], "9": {"uid": "9", "error": "cannot get document summary"}}})
    with pytest.raises(QuarryError) as caught:
        PUBMED.show(replay_client(ReplayTransport([one("9", missing)])), Curie("pubmed", "9"))
    assert caught.value.kind == "not_found"


def test_files_is_unsupported_role_before_any_request(monkeypatch):
    seen = []
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))))
    out = CliRunner().invoke(main, ["--json", "files", "pubmed:38195712"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "unsupported_role" and seen == []


def test_exact_refuses_pubmed_by_name(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))   # any request would fail
    out = CliRunner().invoke(main, ["--json", "search", "--in", "pubmed", "--exact", "x"], catch_exceptions=False)
    assert out.exit_code == 1 and "pubmed" in json.loads(out.stderr)["error"]["detail"]


def test_geo_and_pubmed_share_the_eutils_allowance():                        # spec §12
    clock, times = VirtualClock(), []

    def handle(request):
        times.append(clock.t)
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["1"]}})
        db = request.url.params.get("db") or parse_qs(request.content.decode())["db"][0]
        record = {"uid": "1", "accession": "GSE1", "title": "t"} if db == "gds" else {"uid": "1", "title": "t"}
        return httpx.Response(200, json={"result": {"uids": ["1"], "1": record}})
    adapters = (geo.make(CAT.resources["geo"]), PUBMED, geo.make(CAT.resources["geo"]), PUBMED)
    client = Client(transport=httpx.MockTransport(handle), throttle=Throttle(clock))
    for adapter in adapters:
        adapter.search(client, Query(phrases=("x",)))
    assert len(times) == 8 and all(b - a >= 0.34 - 1e-9 for a, b in zip(times, times[1:]))
    times.clear()
    keyed = Client(transport=httpx.MockTransport(handle), throttle=Throttle(clock), environ={"NCBI_API_KEY": "k"})
    for adapter in adapters:
        adapter.search(keyed, Query(phrases=("x",)))
    assert len(times) == 8 and all(0.1 - 1e-9 <= b - a < 0.34 for a, b in zip(times, times[1:]))


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_is_complete_and_dated():
    cov, rows = PUBMED.search(replay("search-smoldering"), SMOLDERING)
    assert cov.complete == "total" and cov.returned == cov.total == len(rows) > 0
    assert all(r.kind == "publication" and r.id.startswith("pubmed:") and r.date for r in rows)
    assert all(x.split(":")[0] in {"doi", "pmc"} for r in rows for x in r.xrefs)


def test_recorded_show_first():
    with replay("show-first") as client:
        _, rows = PUBMED.search(client, SMOLDERING)
        assert PUBMED.show(client, Curie("pubmed", rows[0].id.split(":", 1)[1])).id == rows[0].id
```

`tests/recordings/pubmed.py`:

```python
"""The PubMed exchanges tests/test_pubmed.py replays."""
from datetime import date

from quarry.ids import Curie
from quarry.query import Query

SMOLDERING = Query(phrases=("smoldering myeloma",), since=date(2024, 1, 1), until=date(2024, 1, 31))


def show_first(adapter, client):
    _, rows = adapter.search(client, SMOLDERING)
    if not rows:
        raise ValueError("PubMed smoldering scenario returned nothing; widen SMOLDERING before recording show-first")
    adapter.show(client, Curie("pubmed", rows[0].id.split(":", 1)[1]))


SCENARIOS = {
    "search-smoldering": lambda adapter, client: adapter.search(client, SMOLDERING),
    "show-first": show_first,
}
```

Run: `just test-one tests/test_pubmed.py`
Expected: FAIL at import (`cannot import name 'pubmed'`).

- [ ] **Step 3: Write the adapter**

`src/quarry/adapters/pubmed.py`:

```python
"""PubMed through E-utilities db=pubmed (spec §13): a literature index searched live per record;
lit owns the snapshot. Phrases are quoted [All Fields] clauses, which PubMed still expands."""
from __future__ import annotations

from collections.abc import Callable

from quarry.adapters import eutils
from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

STRINGS = ("title", "sortpubdate", "fulljournalname")


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    clauses = [eutils.phrases(query)]
    when = eutils.dates(query)
    if when:
        clauses.append(when)
    return " AND ".join(clauses)


def _valid(d: dict) -> dict:
    if type(d.get("uid")) is not str or not d["uid"].isdigit():
        raise UpstreamError("pubmed esummary: uid is not a number")
    for name in STRINGS:
        if d.get(name) is not None and type(d[name]) is not str:
            raise UpstreamError(f"pubmed esummary: {name} is not text")
    ids = d.get("articleids")
    if ids is not None and (type(ids) is not list or any(type(a) is not dict for a in ids)):
        raise UpstreamError("pubmed esummary: articleids is not a list")
    return d


def _row(d: dict) -> Result:
    d = _valid(d)
    ids = {a.get("idtype"): a["value"] for a in d.get("articleids") or [] if type(a.get("value")) is str}
    xrefs = []
    if ids.get("doi"):
        xrefs.append(f"doi:{ids['doi']}")
    if ids.get("pmc"):
        xrefs.append(f"pmc:{ids['pmc']}")
    return Result(id=f"pubmed:{d['uid']}", resource="pubmed", kind="publication", title=d.get("title") or "",
                  date=eutils.iso_date(d.get("sortpubdate")), summary=d.get("fulljournalname") or None, xrefs=tuple(xrefs))


class Pubmed:
    match = "expanded"
    filters = frozenset()

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            count, ids = eutils.esearch(client, self.route, "pubmed", q, query.limit)
            rows = [_row(d) for d in eutils.esummary(client, self.route, "pubmed", ids)]
            complete = completeness(total=count, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, count, len(rows), complete), rows

        return execute

    def show(self, client: Client, curie: Curie) -> Result:
        record = eutils.esummary_one(client, self.route, "pubmed", curie.local)
        if record is None:
            raise QuarryError("not_found", f"{curie} is not in PubMed")
        return _row(record)

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        raise QuarryError("unsupported_role", f"{curie}: pubmed holds publications, which carry no files")


def make(resource: Resource) -> Pubmed:
    return Pubmed(resource)
```

Run: `just test-one tests/test_pubmed.py -k "not recorded"`
Expected: PASS.

- [ ] **Step 4: Record, replay, run the suite, commit**

```bash
just record pubmed
just test-one tests/test_pubmed.py
uv run quarry check
just test-fast
git add src/quarry tests
git commit -m "feat(pubmed): literature search and show over the shared E-utilities allowance"
```

Expected: two fixture files; every test passes; `check` prints nothing.

### Task 5: BioStudies

**Files:**
- Create: `src/quarry/adapters/biostudies.py`, `src/quarry/catalog/resources/biostudies.toml`, `tests/test_biostudies.py`, `tests/recordings/biostudies.py`, `tests/fixtures/biostudies/*.json` (recorded)
- Modify: `src/quarry/catalog/domains/biology.toml` (`resources` gains `biostudies`)

**Interfaces:**
- Consumes: Task 2's `File`, `Adapter.files`, `Client.request(accept=...)`; `completeness`, `Query`, `Result`, `Coverage`, `Curie`.
- Produces: resource `biostudies` owning prefixes `biostudies` and `arrayexpress`; `quarry.adapters.biostudies.compile_query(query) -> tuple[str, dict[str, str]]`, class `Biostudies` (`match = "expanded"`, `filters = frozenset({"organism"})`), `make`.

- [ ] **Step 1: Write the resource file and list BioStudies in biology**

`src/quarry/catalog/resources/biostudies.toml`:

```toml
id = "biostudies"
name = "BioStudies"
roles = ["repository"]
prefixes = ["biostudies", "arrayexpress"]   # S-… studies and the ArrayExpress collection's E-… ids
adapter = "quarry.adapters.biostudies"

[[routes]]
kind = "rest"
url = "https://www.ebi.ac.uk/biostudies/api/v1"
min_interval = 0.5
service = "ebi-biostudies"
auth = "none"
reports_total = true
page_cap = 100

[[pitfalls]]
summary = "phrase queries expand through EFO synonyms with no off switch"
handling = "coverage says match: expanded; --exact refuses BioStudies, so an exact biology search names its resources with --in"
evidence = "mm-159cb7; the search answer lists expandedEfoTerms and expandedSynonyms"

[[pitfalls]]
summary = "search hits omit the organism"
handling = "--organism becomes the facet.organism parameter (a taxon name; an ncbitaxon: CURIE is refused) and every hit then carries it; show reads the study's Organism attribute"
evidence = "mm-159cb7 read one facet listing and one filtered pass per organism value"

[[pitfalls]]
summary = "totalHits is an estimate on large result sets (isTotalHitsExact: false)"
handling = "an inexact total is reported as null and the listing pages to a short page (complete: short-page)"
evidence = "probe 2026-10-10: 'multiple myeloma' reports 10761 inexact hits"

[[pitfalls]]
summary = "E-GEOD-n is the GEO import of GSEn, and S-EPMCn holds a paper's supplementary files"
handling = "arrayexpress:E-GEOD-n carries a geo:GSEn cross-reference; biostudies:S-EPMCn carries pmc:PMCn as its publication"
evidence = "mm-159cb7 mapped E-GEOD to geo: for overlap"
```

In `src/quarry/catalog/domains/biology.toml`: `resources = ["geo", "ena", "biostudies"]`.

- [ ] **Step 2: Write the failing tests**

`tests/test_biostudies.py`:

```python
"""BioStudies: compilation into query and parameters, inexact totals, organism from the facet,
show from the study document, files from nested sections with percent-encoded paths."""
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import biostudies
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.recordings.biostudies import E_GEOD_5900, S_BSST1234, SMOLDERING

FX = Path(__file__).parent / "fixtures" / "biostudies"
CAT = catalog_mod.load()
BS = biostudies.make(CAT.resources["biostudies"])
BASE = "https://www.ebi.ac.uk/biostudies/api/v1"


def test_compile_quotes_ors_puts_dates_in_the_query_and_the_organism_in_a_parameter():
    q = Query(phrases=("multiple myeloma", "MGUS"), filters={"organism": "Homo sapiens"}, since=date(2024, 1, 1))
    assert biostudies.compile_query(q) == ('("multiple myeloma" OR "MGUS") AND release_date:[2024-01-01 TO 3000-12-31]',
                                           {"facet.organism": "Homo sapiens"})
    assert biostudies.compile_query(Query(phrases=("x",))) == ('"x"', {})
    assert biostudies.compile_query(Query(native="accession:E-GEOD-5900")) == ("accession:E-GEOD-5900", {})


@pytest.mark.parametrize("query, kind", [
    (Query(phrases=("x",), filters={"organism": "ncbitaxon:9606"}), "unsupported_filter"),
    (Query(phrases=('a "b"',)), "unsupported_filter"),
])
def test_refusals_happen_in_prepare(query, kind):
    with pytest.raises(QuarryError) as caught:
        BS.prepare(query)
    assert caught.value.kind == kind


def hit(acc, **extra):
    return {"accession": acc, "type": "study", "title": f"t {acc}", "release_date": "2024-01-02", "content": f"{acc} snippet", **extra}


def page(q, n, hits, total, exact, size=100, **params):
    return exchange("GET", f"{BASE}/search", {"query": q, "pageSize": size, "page": n, **params},
                    body=json.dumps({"page": n, "pageSize": size, "totalHits": total, "isTotalHitsExact": exact, "hits": hits}))


def test_exact_total_pages_until_the_total_and_maps_ids_xrefs_and_publication():
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    first = [hit(f"S-BSST{i}") for i in range(100)]
    second = [hit("E-GEOD-5900"), hit("S-EPMC7903229")]
    client = replay_client(ReplayTransport([page(q, 1, first, 102, True), page(q, 2, second, 102, True)]))
    cov, rows = BS.search(client, Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete, cov.match) == (102, 102, "total", "expanded")
    assert rows[100].id == "arrayexpress:E-GEOD-5900" and rows[100].xrefs == ("geo:GSE5900",)
    assert rows[101].id == "biostudies:S-EPMC7903229" and rows[101].publication == "pmc:PMC7903229"
    assert rows[0].domain_fields == {} and rows[0].date == "2024-01-02"


def test_inexact_total_pages_to_a_short_page_and_reports_no_total():
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [hit(f"S-BSST{i}") for i in range(100)], 10761, False),
                                            page(q, 2, [hit("S-BSST200")], 10761, False)]))
    cov, rows = BS.search(client, Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete) == (None, 101, "short-page")


def test_short_listing_under_an_exact_total_fails():
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [hit("S-BSST1")], 5, True)]))
    with pytest.raises(QuarryError) as caught:
        BS.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "incomplete_listing"


def test_limit_asks_for_a_small_page_and_is_limited():
    query = Query(phrases=("x",), limit=2)
    q, _ = biostudies.compile_query(query)
    client = replay_client(ReplayTransport([page(q, 1, [hit("S-BSST1"), hit("S-BSST2")], 50, True, size=2)]))
    cov, rows = BS.search(client, query)
    assert cov.complete == "limited" and len(rows) == 2


def test_organism_facet_annotates_every_row_and_names_itself_in_native():
    query = Query(phrases=("x",), filters={"organism": "Homo sapiens"})
    q, params = biostudies.compile_query(query)
    client = replay_client(ReplayTransport([page(q, 1, [hit("S-BSST1")], 1, True, **params)]))
    cov, rows = BS.search(client, query)
    assert rows[0].domain_fields == {"biology": {"organism": "Homo sapiens"}}
    assert cov.native == '"x" facet.organism=Homo sapiens'


@pytest.mark.parametrize("body", ["<html>", '{"hits": "x"}', '{"hits": [], "totalHits": "1", "isTotalHitsExact": true}',
                                  json.dumps({"hits": [{"accession": 7}], "totalHits": 1, "isTotalHitsExact": True}),
                                  json.dumps({"hits": [hit("WEIRD-1")], "totalHits": 1, "isTotalHitsExact": True})])
def test_malformed_pages_are_upstream_errors(body):
    q, _ = biostudies.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([exchange("GET", f"{BASE}/search", {"query": q, "pageSize": 100, "page": 1}, body=body)]))
    with pytest.raises(QuarryError) as caught:
        BS.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


STUDY = {"accno": "S-BSST1234", "attributes": [{"name": "ReleaseDate", "value": "2024-01-02"}],
         "section": {"type": "Study",
                     "attributes": [{"name": "Title", "value": "T-cell immunity"}, {"name": "Description", "value": "TCR"},
                                    {"name": "Organism", "value": "Mus musculus (mouse)"}],
                     "files": [{"path": "Raw datas.zip", "size": 16, "type": "directory"},
                               {"path": "E7HPV16 raw datas é.zip", "size": 2462872206, "type": "file"}],
                     "links": [[{"url": "GSE5900", "attributes": [{"name": "Type", "value": "GEO"}]}]],
                     "subsections": [{"type": "Assays and Data", "subsections": [
                         {"type": "Data files", "files": [[{"path": "sub/E.idf.txt", "size": 2688, "type": "file"}]]}]}]}}
INFO = {"httpLink": "https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234", "files": 2}


def study(acc, body, status=200):
    return exchange("GET", f"{BASE}/studies/{acc}", status=status, body=body)


def info(acc, body):
    return exchange("GET", f"{BASE}/studies/{acc}/info", body=body)


def test_show_reads_title_date_organism_and_geo_link():
    shown = BS.show(replay_client(ReplayTransport([study("S-BSST1234", json.dumps(STUDY))])), Curie("biostudies", "S-BSST1234"))
    assert shown.as_dict() == {"id": "biostudies:S-BSST1234", "resource": "biostudies", "kind": "dataset", "title": "T-cell immunity",
                               "date": "2024-01-02", "summary": "TCR", "xrefs": ["geo:GSE5900"],
                               "domain_fields": {"biology": {"organism": "Mus musculus (mouse)"}}}


def test_show_404_is_not_found():
    client = replay_client(ReplayTransport([study("S-NOPE1", '{"errorMessage":"Study not found"}', status=404)]))
    with pytest.raises(QuarryError) as caught:
        BS.show(client, Curie("biostudies", "S-NOPE1"))
    assert caught.value.kind == "not_found"


def test_files_walk_nested_sections_skip_directories_and_percent_encode_paths(monkeypatch):     # Review Focus 1
    transport = ReplayTransport([study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps(INFO))])
    record, files = BS.files(replay_client(transport), Curie("biostudies", "S-BSST1234"))
    assert record.title == "T-cell immunity"
    assert [f.as_dict() for f in files] == [
        {"name": "E7HPV16 raw datas é.zip", "size": 2462872206,
         "urls": ["https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/E7HPV16%20raw%20datas%20%C3%A9.zip"]},
        {"name": "sub/E.idf.txt", "size": 2688,
         "urls": ["https://ftp.ebi.ac.uk/pub/databases/biostudies/S-BSST/234/S-BSST1234/Files/sub/E.idf.txt"]}]
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport(
        [study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps(INFO))])))
    out = CliRunner().invoke(main, ["--json", "files", "biostudies:S-BSST1234", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0, out.stderr
    assert [r["name"] for r in json.loads(out.stdout)["resources"]] == ["E7HPV16 raw datas é.zip", "sub/E.idf.txt"]


def test_info_without_an_https_link_is_an_upstream_error():
    transport = ReplayTransport([study("S-BSST1234", json.dumps(STUDY)), info("S-BSST1234", json.dumps({"ftpLink": "ftp://x"}))])
    with pytest.raises(QuarryError) as caught:
        BS.files(replay_client(transport), Curie("biostudies", "S-BSST1234"))
    assert caught.value.kind == "upstream_failed"


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_carries_the_organism_and_canonical_ids():
    cov, rows = BS.search(replay("search-smoldering"), SMOLDERING)
    assert cov.complete in ("total", "short-page") and cov.returned == len(rows) > 0
    assert all(r.id.split(":")[0] in {"biostudies", "arrayexpress"} for r in rows)
    assert all(r.domain_fields["biology"]["organism"] == "Homo sapiens" for r in rows)


def test_recorded_show_e_geod_5900_links_geo():
    shown = BS.show(replay("show-e-geod-5900"), E_GEOD_5900)
    assert shown.id == "arrayexpress:E-GEOD-5900" and "geo:GSE5900" in shown.xrefs
    assert shown.domain_fields["biology"]["organism"] == "Homo sapiens"


def test_recorded_files_s_bsst1234_encode_the_space(monkeypatch):
    _, files = BS.files(replay("files-s-bsst1234"), S_BSST1234)
    assert any(" " in f.name and "%20" in f.urls[0] for f in files)
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-s-bsst1234"))
    out = CliRunner().invoke(main, ["--json", "files", "biostudies:S-BSST1234", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0 and all(" " not in r["url"] for r in json.loads(out.stdout)["resources"])
```

`tests/recordings/biostudies.py`:

```python
"""The BioStudies exchanges tests/test_biostudies.py replays."""
from datetime import date

from quarry.ids import Curie
from quarry.query import Query

SMOLDERING = Query(phrases=("smoldering myeloma",), filters={"organism": "Homo sapiens"},
                   since=date(2023, 1, 1), until=date(2024, 12, 31))
E_GEOD_5900 = Curie("arrayexpress", "E-GEOD-5900")   # a GEO import: Organism attribute, GEO link, files in nested sections
S_BSST1234 = Curie("biostudies", "S-BSST1234")       # a directory entry to skip and a file path with spaces

SCENARIOS = {
    "search-smoldering": lambda adapter, client: adapter.search(client, SMOLDERING),
    "show-e-geod-5900": lambda adapter, client: adapter.show(client, E_GEOD_5900),
    "files-e-geod-5900": lambda adapter, client: adapter.files(client, E_GEOD_5900),
    "files-s-bsst1234": lambda adapter, client: adapter.files(client, S_BSST1234),
}
```

Run: `just test-one tests/test_biostudies.py`
Expected: FAIL at import (`cannot import name 'biostudies'`).

- [ ] **Step 3: Write the adapter**

`src/quarry/adapters/biostudies.py`:

```python
"""BioStudies (spec §13): studies across every collection, ArrayExpress ids under arrayexpress:
and the rest under biostudies:. Phrases are quoted and ORed in `query`, dates go in the query
as release_date, the organism is the facet.organism parameter, and totals count only when
BioStudies says they are exact."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from urllib.parse import quote

from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

CAP = 100
BIOSTUDIES = re.compile(r"^S-[A-Z]{4}[\-\_A-Z\d]+$")          # Bioregistry's biostudies pattern
ARRAYEXPRESS = re.compile(r"^[AEP]-\w{4}-\d+$")               # Bioregistry's arrayexpress pattern
EGEOD = re.compile(r"^E-GEOD-(\d+)$")
EPMC = re.compile(r"^S-EPMC(\d+)$")
GSE = re.compile(r"^GSE\d+$")
TAXON = re.compile(r"^ncbitaxon:", re.IGNORECASE)
HIT_STRINGS = ("title", "release_date", "content")


def _quote(text: str) -> str:
    if '"' in text:
        raise QuarryError("unsupported_filter", f"a BioStudies phrase cannot contain a double quote: {text!r}; use --native")
    return f'"{text}"'


def compile_query(query: Query) -> tuple[str, dict[str, str]]:
    """(the query string, the other search parameters)."""
    organism = query.filters.get("organism")
    if organism and TAXON.match(organism):
        raise QuarryError("unsupported_filter", f"biostudies filters organisms by name, not {organism!r}; "
                                                "give the taxon name, or leave biostudies out with --in")
    params = {"facet.organism": organism} if organism else {}
    if query.native is not None:
        return query.native, params
    q = " OR ".join(_quote(p) for p in query.phrases)
    if query.since or query.until:
        q = f"({q}) AND release_date:[{query.since or '1800-01-01'} TO {query.until or '3000-12-31'}]"
    return q, params


def _id(accession: str) -> str:
    if ARRAYEXPRESS.match(accession):
        return f"arrayexpress:{accession}"
    if BIOSTUDIES.match(accession):
        return f"biostudies:{accession}"
    raise UpstreamError("biostudies: an accession matches neither the biostudies nor the arrayexpress pattern")


def _row(h: dict, organism: str | None) -> Result:
    if type(h.get("accession")) is not str:
        raise UpstreamError("biostudies search: a hit has no accession")
    for name in HIT_STRINGS:
        if h.get(name) is not None and type(h[name]) is not str:
            raise UpstreamError(f"biostudies search: {name} is not text")
    acc = h["accession"]
    xrefs = [f"geo:GSE{m.group(1)}" for m in [EGEOD.match(acc)] if m]
    paper = EPMC.match(acc)
    return Result(id=_id(acc), resource="biostudies", kind="dataset", title=h.get("title") or "",
                  date=h.get("release_date") or None, summary=h.get("content") or None, xrefs=tuple(xrefs),
                  publication=f"pmc:PMC{paper.group(1)}" if paper else None,
                  domain_fields={"biology": {"organism": organism}} if organism else {})


def _json(text: str, what: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"biostudies {what}: not JSON") from None
    if type(data) is not dict:
        raise UpstreamError(f"biostudies {what}: expected a JSON object")
    return data


def _page(text: str) -> dict:
    data = _json(text, "search")
    if (type(data.get("hits")) is not list or any(type(h) is not dict for h in data["hits"])
            or type(data.get("totalHits")) is not int or type(data.get("isTotalHitsExact")) is not bool):
        raise UpstreamError("biostudies search: expected hits, totalHits and isTotalHitsExact")
    return data


def _attributes(items: object) -> dict[str, str]:
    """name -> value over a BioStudies attribute list; later names win."""
    out: dict[str, str] = {}
    for item in items if type(items) is list else []:
        if type(item) is dict and type(item.get("name")) is str and type(item.get("value")) is str:
            out[item["name"]] = item["value"]
    return out


def _collect(node: object, key: str) -> Iterator[dict]:
    """Every dict under `key` anywhere in the document; BioStudies nests them in lists of lists."""
    if type(node) is dict:
        for name, value in node.items():
            if name == key:
                yield from _leaves(value)
            else:
                yield from _collect(value, key)
    elif type(node) is list:
        for item in node:
            yield from _collect(item, key)


def _leaves(value: object) -> Iterator[dict]:
    if type(value) is dict:
        yield value
    elif type(value) is list:
        for item in value:
            yield from _leaves(item)


class Biostudies:
    match = "expanded"
    filters = frozenset({"organism"})

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q, params = compile_query(query)
        organism = params.get("facet.organism")
        native = q if not params else f"{q} facet.organism={organism}"

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            size = min(CAP, query.limit) if query.limit else CAP
            rows, page, exact_total = [], 1, None
            while True:
                data = _page(client.request(self.route, "GET", "search",
                                            params={"query": q, "pageSize": size, "page": page, **params}).text)
                hits = data["hits"]
                exact_total = data["totalHits"] if data["isTotalHitsExact"] else None
                rows += [_row(h, organism) for h in hits]
                short = len(hits) < size
                if short or (query.limit and len(rows) >= query.limit) or (exact_total is not None and len(rows) >= exact_total):
                    break
                page += 1
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=exact_total, returned=len(rows), limit=query.limit, short_page=short)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, native, match, exact_total, len(rows), complete), rows

        return execute

    def _study(self, client: Client, curie: Curie) -> tuple[Result, list[tuple[str, int | None]]]:
        """The record and its (path, size) file entries; directories are not files."""
        response = client.request(self.route, "GET", f"studies/{curie.local}", accept=frozenset({404}))
        if response.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in BioStudies")
        doc = _json(response.text, "study")
        attrs = _attributes(doc.get("attributes")) | _attributes(doc.get("section", {}).get("attributes")
                                                                 if type(doc.get("section")) is dict else None)
        xrefs = [f"geo:GSE{m.group(1)}" for m in [EGEOD.match(curie.local)] if m]
        xrefs += [f"geo:{link['url']}" for link in _collect(doc, "links")
                  if _attributes(link.get("attributes")).get("Type") == "GEO" and type(link.get("url")) is str
                  and GSE.match(link["url"]) and f"geo:{link['url']}" not in xrefs]
        paper = EPMC.match(curie.local)
        organism = attrs.get("Organism")
        record = Result(id=str(curie), resource="biostudies", kind="dataset", title=attrs.get("Title") or "",
                        date=attrs.get("ReleaseDate") or None, summary=attrs.get("Description") or None, xrefs=tuple(xrefs),
                        publication=f"pmc:PMC{paper.group(1)}" if paper else None,
                        domain_fields={"biology": {"organism": organism}} if organism else {})
        entries = []
        for entry in _collect(doc, "files"):
            if entry.get("type") != "file":
                continue
            if type(entry.get("path")) is not str or not entry["path"]:
                raise UpstreamError("biostudies study: a file has no path")
            size = entry.get("size")
            if size is not None and (type(size) is not int or size < 0):
                raise UpstreamError("biostudies study: a file size is not a number")
            entries.append((entry["path"], size))
        return record, entries

    def show(self, client: Client, curie: Curie) -> Result:
        return self._study(client, curie)[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        record, entries = self._study(client, curie)
        info = _json(client.request(self.route, "GET", f"studies/{curie.local}/info").text, "info")
        base = info.get("httpLink")
        if type(base) is not str or not base.startswith("https://"):
            raise UpstreamError("biostudies info: no https link to the study's files")
        return record, [File(name=path, urls=(f"{base.rstrip('/')}/Files/{quote(path)}",), size=size) for path, size in entries]


def make(resource: Resource) -> Biostudies:
    return Biostudies(resource)
```

Run: `just test-one tests/test_biostudies.py -k "not recorded"`
Expected: PASS.

- [ ] **Step 4: Record, replay, run the suite, commit**

```bash
just record biostudies
ls -la tests/fixtures/biostudies/
just test-one tests/test_biostudies.py
uv run quarry check
just test-fast
git add src/quarry tests
git commit -m "feat(biostudies): studies with facet organisms, GEO links and nested file sections"
```

Expected: four fixture files; if `search-smoldering.json` exceeds about 500 KB, narrow
`SMOLDERING`'s window and record again; if it holds no hits, widen the window (the recorded test
needs at least one human study). Every test passes; `check` prints nothing.

### Task 6: Zenodo

**Files:**
- Create: `src/quarry/adapters/zenodo.py`, `src/quarry/catalog/resources/zenodo.toml`, `tests/test_zenodo.py`, `tests/recordings/common.py`, `tests/recordings/zenodo.py`, `tests/fixtures/zenodo/*.json` (recorded)
- Modify: `src/quarry/catalog/domains/core.toml` (`resources` gains `zenodo`)

**Interfaces:**
- Consumes: Task 2's `File`, `Adapter.files`, `Coverage.warnings`, `Client.request(accept=...)`.
- Produces: resource `zenodo` owning `zenodo.record`; `quarry.adapters.zenodo.compile_query`, class `Zenodo` (`match = "exact"`, `filters = frozenset()`), `make`; `tests/recordings/common.py::CORE_MYELOMA`, the one query Task 7 records too and Task 8 searches across `core`.

- [ ] **Step 1: Write the resource file and list Zenodo in core**

`src/quarry/catalog/resources/zenodo.toml`:

```toml
id = "zenodo"
name = "Zenodo"
roles = ["repository"]
prefixes = ["zenodo.record"]           # Bioregistry's prefix; `zenodo` is its synonym
adapter = "quarry.adapters.zenodo"

[[routes]]
kind = "rest"
url = "https://zenodo.org/api"
min_interval = 1.0
service = "zenodo"
auth = "optional-key"
key_env = "ZENODO_TOKEN"
key_param = "access_token"
keyed_min_interval = 0.6
reports_total = true
page_cap = 25

[[pitfalls]]
summary = "a page size above 25 is HTTP 400 without a token"
handling = "every listing pages by 25, with or without a token"
evidence = "mm-159cb7; probe 2026-10-10: size=26 answered 400 'Page size cannot be greater than 25'"

[[pitfalls]]
summary = "the search index serves at most 10,000 results per query"
handling = "a total above 10,000 is refused with too_many_results unless -n keeps the listing inside it"
evidence = "Zenodo REST API documentation (the Elasticsearch result window)"

[[pitfalls]]
summary = "a record's own DOI and its paper's DOI are both DOIs"
handling = "a related identifier whose relation is isSupplementTo, isDescribedBy or isCitedBy and whose resource type is a publication gives the publication; the record's DOI and every other related DOI are xrefs"
evidence = "record 15025566: isDescribedBy 10.1038/s43018-025-01072-4 beside doi 10.5281/zenodo.15025566"
```

In `src/quarry/catalog/domains/core.toml`: `resources = ["zenodo", "pubmed"]`.

- [ ] **Step 2: Write the failing tests**

`tests/recordings/common.py`:

```python
"""One query the core repositories record alike, so Task 8 can search core across their fixtures."""
from datetime import date

from quarry.query import Query

CORE_MYELOMA = Query(phrases=("multiple myeloma",), since=date(2026, 9, 1), until=date(2026, 10, 10))
```

`tests/recordings/zenodo.py`:

```python
"""The Zenodo exchanges tests/test_zenodo.py replays."""
from quarry.ids import Curie
from tests.recordings.common import CORE_MYELOMA

OPEN = Curie("zenodo.record", "21628233")          # one zip file with an md5; a related dataset DOI, no paper
RESTRICTED = Curie("zenodo.record", "15025566")    # no files; isDescribedBy a publication-article

SCENARIOS = {
    "search-core": lambda adapter, client: adapter.search(client, CORE_MYELOMA),
    "show-open": lambda adapter, client: adapter.show(client, OPEN),
    "files-open": lambda adapter, client: adapter.files(client, OPEN),
    "show-restricted": lambda adapter, client: adapter.show(client, RESTRICTED),
    "files-restricted": lambda adapter, client: adapter.files(client, RESTRICTED),
}
```

`tests/test_zenodo.py`:

```python
"""Zenodo: compilation, the 25-record page, the 10,000 window, publication versus xrefs, files."""
import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import zenodo
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.recordings.common import CORE_MYELOMA
from tests.recordings.zenodo import OPEN, RESTRICTED

FX = Path(__file__).parent / "fixtures" / "zenodo"
CAT = catalog_mod.load()
ZEN = zenodo.make(CAT.resources["zenodo"])
BASE = "https://zenodo.org/api"


def test_compile_quotes_ors_and_puts_dates_in_q():
    assert zenodo.compile_query(Query(phrases=("multiple myeloma", "MGUS"))) == '"multiple myeloma" OR "MGUS"'
    assert zenodo.compile_query(Query(phrases=("x",), since=date(2024, 1, 1), until=date(2024, 3, 31))) == \
        '("x") AND publication_date:[2024-01-01 TO 2024-03-31]'
    assert zenodo.compile_query(Query(phrases=("x",), until=date(2024, 3, 31))) == '("x") AND publication_date:[* TO 2024-03-31]'
    assert zenodo.compile_query(Query(native="title:x")) == "title:x"


@pytest.mark.parametrize("phrase", ['a "b"', "a\\b"])
def test_quotes_and_backslashes_are_refused_in_prepare(phrase):
    with pytest.raises(QuarryError) as caught:
        ZEN.prepare(Query(phrases=(phrase,)))
    assert caught.value.kind == "unsupported_filter"


def record(n, related=(), files=(), doi=True):
    return {"id": n, "doi": f"10.5281/zenodo.{n}" if doi else None,
            "metadata": {"title": f"r{n}", "publication_date": "2026-07-27", "description": "<p>d</p>",
                         "resource_type": {"type": "dataset", "title": "Dataset"}, "related_identifiers": list(related)},
            "files": list(files)}


def page(q, n, hits, total, size=25):
    return exchange("GET", f"{BASE}/records", {"q": q, "type": "dataset", "sort": "mostrecent", "size": size, "page": n},
                    body=json.dumps({"hits": {"hits": hits, "total": total}}))


def test_pages_by_25_until_the_total_and_maps_ids_and_xrefs():
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [record(i) for i in range(25)], 27), page(q, 2, [record(25), record(26)], 27)]))
    cov, rows = ZEN.search(client, Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete, cov.match) == (27, 27, "total", "exact")
    assert rows[0].id == "zenodo.record:0" and rows[0].xrefs == ("doi:10.5281/zenodo.0",) and rows[0].summary == "d"
    assert rows[0].date == "2026-07-27" and rows[0].kind == "dataset"


def test_limit_asks_for_a_small_page_and_is_limited():
    query = Query(phrases=("x",), limit=2)
    q = zenodo.compile_query(query)
    cov, rows = ZEN.search(replay_client(ReplayTransport([page(q, 1, [record(1), record(2)], 40, size=2)])), query)
    assert cov.complete == "limited" and len(rows) == 2


def test_total_beyond_the_window_is_refused_unless_limited():
    q = zenodo.compile_query(Query(phrases=("x",)))
    with pytest.raises(QuarryError) as caught:
        ZEN.search(replay_client(ReplayTransport([page(q, 1, [record(1)], 10_001)])), Query(phrases=("x",)))
    assert caught.value.kind == "too_many_results"
    query = Query(phrases=("x",), limit=1)
    cov, _ = ZEN.search(replay_client(ReplayTransport([page(q, 1, [record(1)], 10_001, size=1)])), query)
    assert cov.complete == "limited"


def test_publication_is_the_first_paper_and_the_rest_are_xrefs_with_a_warning():
    paper = {"identifier": "10.1038/a", "relation": "isSupplementTo", "resource_type": "publication-article", "scheme": "doi"}
    second = {"identifier": "10.1038/b", "relation": "isDescribedBy", "resource_type": "publication-preprint", "scheme": "doi"}
    dataset = {"identifier": "10.7937/k4qv-hh78", "relation": "isDescribedBy", "resource_type": "dataset", "scheme": "doi"}
    url = {"identifier": "https://example.org", "relation": "isSupplementTo", "resource_type": "publication-article", "scheme": "url"}
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [record(1, related=[dataset, paper, second, url])], 1)]))
    cov, rows = ZEN.search(client, Query(phrases=("x",)))
    assert rows[0].publication == "doi:10.1038/a"
    assert rows[0].xrefs == ("doi:10.5281/zenodo.1", "doi:10.1038/b", "doi:10.7937/k4qv-hh78")
    assert cov.warnings == ("zenodo.record:1 names 2 papers; doi:10.1038/a is its publication and the rest are xrefs",)


@pytest.mark.parametrize("body", ["<html>", '{"hits": []}', json.dumps({"hits": {"hits": [{"id": "x"}], "total": 1}}),
                                  json.dumps({"hits": {"hits": [{**record(1), "metadata": "x"}], "total": 1}}),
                                  json.dumps({"hits": {"hits": [record(1)], "total": "1"}})])
def test_malformed_pages_are_upstream_errors(body):
    q = zenodo.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([exchange("GET", f"{BASE}/records", {"q": q, "type": "dataset", "sort": "mostrecent",
                                                                                "size": 25, "page": 1}, body=body)]))
    with pytest.raises(QuarryError) as caught:
        ZEN.search(client, Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


FILE = {"id": "6e3c", "key": "seg.zip", "size": 6057052, "checksum": "md5:3ee596a3c8d9b37ab36986557ee63666",
        "links": {"self": "https://zenodo.org/api/records/21628233/files/seg.zip/content"}}


def one(n, body, status=200):
    return exchange("GET", f"{BASE}/records/{n}", status=status, body=body)


def test_show_and_files_from_the_record_document():
    client = replay_client(ReplayTransport([one(21628233, json.dumps(record(21628233, files=[FILE])))]))
    record_, files = ZEN.files(client, Curie("zenodo.record", "21628233"))
    assert record_.id == "zenodo.record:21628233"
    assert [f.as_dict() for f in files] == [{"name": "seg.zip", "size": 6057052, "checksums": {"md5": "3ee596a3c8d9b37ab36986557ee63666"},
                                            "urls": ["https://zenodo.org/api/records/21628233/files/seg.zip/content"]}]
    client = replay_client(ReplayTransport([one(21628233, json.dumps(record(21628233)))]))
    assert ZEN.show(client, Curie("zenodo.record", "21628233")).title == "r21628233"


def test_show_404_is_not_found():
    client = replay_client(ReplayTransport([one(9, '{"status": 404, "message": "The persistent identifier does not exist."}', status=404)]))
    with pytest.raises(QuarryError) as caught:
        ZEN.show(client, Curie("zenodo.record", "9"))
    assert caught.value.kind == "not_found"


@pytest.mark.parametrize("bad", [{**FILE, "links": {"self": "ftp://x"}}, {**FILE, "key": 7}, {**FILE, "size": "big"}, {**FILE, "checksum": "md5"}])
def test_malformed_files_are_upstream_errors(bad):
    client = replay_client(ReplayTransport([one(1, json.dumps(record(1, files=[bad])))]))
    with pytest.raises(QuarryError) as caught:
        ZEN.files(client, Curie("zenodo.record", "1"))
    assert caught.value.kind == "upstream_failed"


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_pages_by_25_and_is_complete():
    cov, rows = ZEN.search(replay("search-core"), CORE_MYELOMA)
    assert cov.complete == "total" and cov.returned == cov.total == len(rows)
    assert all(r.id.startswith("zenodo.record:") for r in rows)


def test_recorded_open_record_has_an_md5_file_and_no_paper(monkeypatch):
    shown = ZEN.show(replay("show-open"), OPEN)
    assert shown.publication is None and "doi:10.7937/k4qv-hh78" in shown.xrefs
    _, files = ZEN.files(replay("files-open"), OPEN)
    assert len(files) == 1 and "md5" in files[0].checksums
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-open"))
    out = CliRunner().invoke(main, ["--json", "files", str(OPEN), "--acquisition"], catch_exceptions=False)
    request = json.loads(out.stdout)
    assert out.exit_code == 0 and request["locator"] == "accession:zenodo.record:21628233" and "expected" not in request["resources"][0]


def test_recorded_restricted_record_has_a_paper_and_no_files(monkeypatch):
    assert ZEN.show(replay("show-restricted"), RESTRICTED).publication == "doi:10.1038/s43018-025-01072-4"
    assert ZEN.files(replay("files-restricted"), RESTRICTED)[1] == []
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-restricted"))
    out = CliRunner().invoke(main, ["--json", "files", str(RESTRICTED), "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "no_files"
```

Run: `just test-one tests/test_zenodo.py`
Expected: FAIL at import (`cannot import name 'zenodo'`).

- [ ] **Step 3: Write the adapter**

`src/quarry/adapters/zenodo.py`:

```python
"""Zenodo (spec §13): dataset records through the records API. Phrases are quoted and ORed in
`q`, dates are a publication_date range inside it, pages are 25 with or without a token, and a
paper is found among the related identifiers."""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

CAP = 25
WINDOW = 10_000
TAGS = re.compile(r"<[^>]+>")
PAPER_RELATIONS = frozenset({"issupplementto", "isdescribedby", "iscitedby"})


def _quote(text: str) -> str:
    for char, what in (('"', "a double quote"), ("\\", "a backslash")):
        if char in text:
            raise QuarryError("unsupported_filter", f"a Zenodo phrase cannot contain {what}: {text!r}; use --native")
    return f'"{text}"'


def compile_query(query: Query) -> str:
    if query.native is not None:
        return query.native
    q = " OR ".join(_quote(p) for p in query.phrases)
    if query.since or query.until:
        q = f"({q}) AND publication_date:[{query.since or '*'} TO {query.until or '*'}]"
    return q


def _json(text: str, what: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise UpstreamError(f"zenodo {what}: not JSON") from None
    if type(data) is not dict:
        raise UpstreamError(f"zenodo {what}: expected a JSON object")
    return data


def _valid(h: dict) -> dict:
    m = h.get("metadata")
    if type(h.get("id")) is not int or type(m) is not dict:
        raise UpstreamError("zenodo record: id or metadata missing")
    for name in ("title", "publication_date", "description"):
        if m.get(name) is not None and type(m[name]) is not str:
            raise UpstreamError(f"zenodo record: {name} is not text")
    if h.get("doi") is not None and type(h["doi"]) is not str:
        raise UpstreamError("zenodo record: doi is not text")
    related = m.get("related_identifiers")
    if related is not None and (type(related) is not list or any(type(r) is not dict for r in related)):
        raise UpstreamError("zenodo record: related_identifiers is not a list")
    if h.get("files") is not None and (type(h["files"]) is not list or any(type(f) is not dict for f in h["files"])):
        raise UpstreamError("zenodo record: files is not a list")
    return h


def _row(h: dict) -> tuple[Result, str | None]:
    """The result and, when the record names several papers, the warning the report carries."""
    h = _valid(h)
    m = h["metadata"]
    papers, others = [], []
    for rel in m.get("related_identifiers") or []:
        if rel.get("scheme") != "doi" or type(rel.get("identifier")) is not str:
            continue
        is_paper = (str(rel.get("relation", "")).lower() in PAPER_RELATIONS
                    and str(rel.get("resource_type", "")).startswith("publication"))
        (papers if is_paper else others).append(f"doi:{rel['identifier']}")
    xrefs = ([f"doi:{h['doi']}"] if h.get("doi") else []) + papers[1:] + others
    result = Result(id=f"zenodo.record:{h['id']}", resource="zenodo", kind="dataset", title=m.get("title") or "",
                    date=m.get("publication_date") or None, summary=TAGS.sub("", m.get("description") or "").strip() or None,
                    xrefs=tuple(dict.fromkeys(xrefs)), publication=papers[0] if papers else None)
    warning = (f"{result.id} names {len(papers)} papers; {papers[0]} is its publication and the rest are xrefs"
               if len(papers) > 1 else None)
    return result, warning


def _files(h: dict) -> list[File]:
    files = []
    for f in h.get("files") or []:
        url = (f.get("links") or {}).get("self") if type(f.get("links")) is dict else None
        if type(f.get("key")) is not str or not f["key"] or type(url) is not str or not url.startswith("https://"):
            raise UpstreamError("zenodo files: a file needs a key and an https self link")
        if f.get("size") is not None and (type(f["size"]) is not int or f["size"] < 0):
            raise UpstreamError("zenodo files: a size is not a number")
        checksums = {}
        if f.get("checksum") is not None:
            algorithm, sep, digest = str(f["checksum"]).partition(":")
            if not sep or not digest:
                raise UpstreamError("zenodo files: a checksum is not <algorithm>:<digest>")
            checksums[algorithm] = digest
        files.append(File(name=f["key"], urls=(url,), size=f.get("size"), checksums=checksums))
    return files


class Zenodo:
    match = "exact"
    filters = frozenset()

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q = compile_query(query)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            size = min(CAP, query.limit) if query.limit else CAP
            rows, warnings, page = [], [], 1
            while True:
                data = _json(client.request(self.route, "GET", "records", params={
                    "q": q, "type": "dataset", "sort": "mostrecent", "size": size, "page": page}).text, "search")
                hits = data.get("hits")
                if type(hits) is not dict or type(hits.get("hits")) is not list or type(hits.get("total")) is not int:
                    raise UpstreamError("zenodo search: expected hits.hits and hits.total")
                total = hits["total"]
                if total > WINDOW and (query.limit is None or query.limit > WINDOW):
                    raise QuarryError("too_many_results", f"{total} records match, more than the {WINDOW} Zenodo serves "
                                                          f"per query; narrow the query or pass -n")
                for h in hits["hits"]:
                    result, warning = _row(h)
                    rows.append(result)
                    if warning:
                        warnings.append(warning)
                wanted = min(total, query.limit) if query.limit else total
                if not hits["hits"] or len(rows) >= wanted:
                    break
                page += 1
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=total, returned=len(rows), limit=query.limit, short_page=False)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, total, len(rows), complete, warnings=tuple(warnings)), rows

        return execute

    def _record(self, client: Client, curie: Curie) -> dict:
        response = client.request(self.route, "GET", f"records/{curie.local}", accept=frozenset({404}))
        if response.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in Zenodo")
        return _json(response.text, "record")

    def show(self, client: Client, curie: Curie) -> Result:
        return _row(self._record(client, curie))[0]

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        doc = self._record(client, curie)
        return _row(doc)[0], _files(doc)


def make(resource: Resource) -> Zenodo:
    return Zenodo(resource)
```

Run: `just test-one tests/test_zenodo.py -k "not recorded"`
Expected: PASS.

- [ ] **Step 4: Record, replay, run the suite, commit**

```bash
just record zenodo          # ZENODO_TOKEN may be set; the fixtures redact it and pages stay 25
just test-one tests/test_zenodo.py
uv run quarry check
just test-fast
git add src/quarry tests
git commit -m "feat(zenodo): dataset records by 25, papers apart from DOIs, files with md5"
```

Expected: five fixture files; every test passes; `check` prints nothing.

### Task 7: figshare

**Files:**
- Create: `src/quarry/adapters/figshare.py`, `src/quarry/catalog/resources/figshare.toml`, `tests/test_figshare.py`, `tests/recordings/figshare.py`, `tests/fixtures/figshare/*.json` (recorded)
- Modify: `src/quarry/catalog/domains/core.toml` (`resources` gains `figshare`)

**Interfaces:**
- Consumes: Task 2's `File`, `Adapter.files`, `Client.request(json=..., accept=...)`; Task 6's `tests/recordings/common.py::CORE_MYELOMA`.
- Produces: resource `figshare` with local prefix `figshare`; `quarry.adapters.figshare.compile_query(query) -> str` (the `search_for` string), class `Figshare` (`match = "exact"`, `filters = frozenset()`), `make`.

- [ ] **Step 1: Write the resource file and list figshare in core**

`src/quarry/catalog/resources/figshare.toml`:

```toml
id = "figshare"
name = "figshare"
roles = ["repository"]
adapter = "quarry.adapters.figshare"

[local_prefixes.figshare]
pattern = "^\\d+$"
uri_format = "https://figshare.com/articles/dataset/_/$1"
why = "Bioregistry has no figshare prefix (checked 2026-10-10); items are numbered and resolve by number"

[[routes]]
kind = "rest"
url = "https://api.figshare.com/v2"
min_interval = 1.0
service = "figshare"
auth = "none"
reports_total = false
page_cap = 100

[[pitfalls]]
summary = "the search endpoint reports no total"
handling = "paging ends on the first short page and coverage says complete: short-page"
evidence = "mm-159cb7"

[[pitfalls]]
summary = "published_since and published_until on articles/search are not applied"
handling = "--since and --until order the listing by published_date and bound it on the client: newest first, stopping below --since; oldest first, stopping above --until, when only --until is given"
evidence = "probe 2026-10-10: published_until 2020-01-01 returned items published 2026-10-10"

[[pitfalls]]
summary = "a publisher supplement is one item per table or figure, each with its own DOI beside the paper's"
handling = "the item keeps its own id (figshare:n) with its DOI in xrefs; resource_doi is the publication; grouping supplements under their paper is the consumer's step"
evidence = "figshare:34254439 has doi 10.1371/journal.pone.0360063.t007 and resource_doi 10.1371/journal.pone.0360063"
```

In `src/quarry/catalog/domains/core.toml`: `resources = ["zenodo", "figshare", "pubmed"]`.

- [ ] **Step 2: Write the failing tests**

`tests/recordings/figshare.py`:

```python
"""The figshare exchanges tests/test_figshare.py replays."""
from quarry.ids import Curie
from tests.recordings.common import CORE_MYELOMA

SUPPLEMENT = Curie("figshare", "34254439")     # a PLOS table: its own DOI (.t007) beside the paper's resource_doi

SCENARIOS = {
    "search-core": lambda adapter, client: adapter.search(client, CORE_MYELOMA),
    "show-supplement": lambda adapter, client: adapter.show(client, SUPPLEMENT),
    "files-supplement": lambda adapter, client: adapter.files(client, SUPPLEMENT),
}
```

`tests/test_figshare.py`:

```python
"""figshare: the POSTed search, short-page completeness, client-side date bounds over a
published_date ordering, the two-DOI supplement, files with md5 and media type."""
import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.adapters import figshare
from quarry.cli import main
from quarry.errors import QuarryError
from quarry.ids import Curie
from quarry.query import Query
from quarry.testing import ReplayTransport, exchange, replay_client
from tests.recordings.common import CORE_MYELOMA
from tests.recordings.figshare import SUPPLEMENT

FX = Path(__file__).parent / "fixtures" / "figshare"
CAT = catalog_mod.load()
FIG = figshare.make(CAT.resources["figshare"])
BASE = "https://api.figshare.com/v2"


def test_compile_quotes_and_ors_phrases_only():
    assert figshare.compile_query(Query(phrases=("multiple myeloma", "MGUS"))) == '"multiple myeloma" OR "MGUS"'
    assert figshare.compile_query(Query(phrases=("x",), since=date(2024, 1, 1))) == '"x"'
    assert figshare.compile_query(Query(native=":title: x")) == ":title: x"
    with pytest.raises(QuarryError) as caught:
        FIG.prepare(Query(phrases=('a "b"',)))
    assert caught.value.kind == "unsupported_filter"


def item(n, published, doi="", resource_doi=""):
    return {"id": n, "title": f"<p>t{n}</p>", "doi": doi, "published_date": f"{published}T10:00:00Z", "defined_type_name": "dataset",
            "resource_doi": resource_doi, "resource_title": "", "url": f"{BASE}/articles/{n}"}


def page(q, n, items, size=100, direction="desc"):
    body = {"search_for": q, "item_type": 3, "page_size": size, "page": n, "order": "published_date", "order_direction": direction}
    return exchange("POST", f"{BASE}/articles/search", json_body=body, body=json.dumps(items))


def test_pages_until_a_short_page_and_keeps_item_ids_with_doi_xrefs_and_paper_publication():
    q = figshare.compile_query(Query(phrases=("x",)))
    first = [item(i, "2026-10-10") for i in range(100)]
    second = [item(34254439, "2026-10-08", doi="10.1371/journal.pone.0360063.t007", resource_doi="10.1371/journal.pone.0360063")]
    cov, rows = FIG.search(replay_client(ReplayTransport([page(q, 1, first), page(q, 2, second)])), Query(phrases=("x",)))
    assert (cov.total, cov.returned, cov.complete, cov.match) == (None, 101, "short-page", "exact")
    last = rows[-1]
    assert last.id == "figshare:34254439" and last.title == "t34254439" and last.date == "2026-10-08"
    assert last.publication == "doi:10.1371/journal.pone.0360063" and last.xrefs == ("doi:10.1371/journal.pone.0360063.t007",)
    assert rows[0].publication is None and rows[0].xrefs == ()


def test_since_orders_newest_first_skips_newer_than_until_and_stops_below_since():
    query = Query(phrases=("x",), since=date(2026, 9, 1), until=date(2026, 10, 10))
    q = figshare.compile_query(query)
    items = [item(1, "2026-10-11"), item(2, "2026-10-10"), item(3, "2026-09-01"), item(4, "2026-08-31"), item(5, "2026-01-01")]
    cov, rows = FIG.search(replay_client(ReplayTransport([page(q, 1, items)])), query)
    assert [r.id for r in rows] == ["figshare:2", "figshare:3"] and cov.complete == "short-page"


def test_until_alone_orders_oldest_first_and_stops_above_until():
    query = Query(phrases=("x",), until=date(2010, 1, 1))
    q = figshare.compile_query(query)
    items = [item(1, "2008-06-20"), item(2, "2009-04-13"), item(3, "2010-01-02")]
    cov, rows = FIG.search(replay_client(ReplayTransport([page(q, 1, items, direction="asc")])), query)
    assert [r.id for r in rows] == ["figshare:1", "figshare:2"] and cov.complete == "short-page"


def test_limit_asks_for_a_small_page_and_is_limited():
    query = Query(phrases=("x",), limit=2)
    q = figshare.compile_query(query)
    cov, rows = FIG.search(replay_client(ReplayTransport([page(q, 1, [item(1, "2026-01-01"), item(2, "2026-01-01")], size=2)])), query)
    assert cov.complete == "limited" and len(rows) == 2


def test_an_exactly_full_last_page_needs_one_more_empty_page():
    q = figshare.compile_query(Query(phrases=("x",)))
    client = replay_client(ReplayTransport([page(q, 1, [item(i, "2026-01-01") for i in range(100)]), page(q, 2, [])]))
    cov, rows = FIG.search(client, Query(phrases=("x",)))
    assert cov.complete == "short-page" and len(rows) == 100


@pytest.mark.parametrize("body", ["<html>", '{"items": []}', json.dumps([{"id": "x"}]), json.dumps([{**item(1, "2026-01-01"), "published_date": "yesterday"}])])
def test_malformed_pages_are_upstream_errors(body):
    q = figshare.compile_query(Query(phrases=("x",)))
    bad = exchange("POST", f"{BASE}/articles/search", json_body={"search_for": q, "item_type": 3, "page_size": 100, "page": 1,
                                                                 "order": "published_date", "order_direction": "desc"}, body=body)
    with pytest.raises(QuarryError) as caught:
        FIG.search(replay_client(ReplayTransport([bad])), Query(phrases=("x",)))
    assert caught.value.kind == "upstream_failed"


ARTICLE = {**item(34254439, "2026-10-08", doi="10.1371/journal.pone.0360063.t007", resource_doi="10.1371/journal.pone.0360063"),
           "description": "<p>Logistic regression</p>",
           "files": [{"id": 69801769, "name": "Table 7.xls", "size": 5632, "is_link_only": False,
                      "download_url": "https://ndownloader.figshare.com/files/69801769",
                      "supplied_md5": "e9c5", "computed_md5": "e9c501729209a445c78cfd70d7a30f7a", "mimetype": "application/vnd.ms-excel"},
                     {"id": 1, "name": "elsewhere.bam", "size": 0, "is_link_only": True, "download_url": "", "computed_md5": "", "mimetype": ""}]}


def one(n, body, status=200):
    return exchange("GET", f"{BASE}/articles/{n}", status=status, body=body)


def test_show_has_the_description_and_the_two_dois_apart():                 # the §12 pitfall, synthetic
    shown = FIG.show(replay_client(ReplayTransport([one(34254439, json.dumps(ARTICLE))])), SUPPLEMENT)
    assert shown.summary == "Logistic regression" and shown.publication == "doi:10.1371/journal.pone.0360063"
    assert shown.xrefs == ("doi:10.1371/journal.pone.0360063.t007",)


def test_files_carry_md5_media_type_and_no_url_for_link_only(monkeypatch):     # Review Focus 3
    _, files = FIG.files(replay_client(ReplayTransport([one(34254439, json.dumps(ARTICLE))])), SUPPLEMENT)
    assert [f.as_dict() for f in files] == [
        {"name": "Table 7.xls", "urls": ["https://ndownloader.figshare.com/files/69801769"], "size": 5632,
         "checksums": {"md5": "e9c501729209a445c78cfd70d7a30f7a"}, "media_type": "application/vnd.ms-excel"},
        {"name": "elsewhere.bam", "urls": [], "size": 0}]
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([one(34254439, json.dumps(ARTICLE))])))
    out = CliRunner().invoke(main, ["--json", "files", "figshare:34254439", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 1 and "elsewhere.bam" in json.loads(out.stderr)["error"]["detail"]
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([one(34254439, json.dumps(ARTICLE))])))
    out = CliRunner().invoke(main, ["--json", "files", "figshare:34254439", "--acquisition", "--file", "*.xls"], catch_exceptions=False)
    assert out.exit_code == 0 and json.loads(out.stdout)["resources"] == [
        {"name": "Table 7.xls", "url": "https://ndownloader.figshare.com/files/69801769"}]


def test_show_404_is_not_found():
    client = replay_client(ReplayTransport([one(9, '{"message": "Entity not found: ArticleVersion", "code": "EntityNotFound"}', status=404)]))
    with pytest.raises(QuarryError) as caught:
        FIG.show(client, Curie("figshare", "9"))
    assert caught.value.kind == "not_found"


def replay(*names):
    return replay_client(ReplayTransport.from_files([FX / f"{n}.json" for n in names]))


def test_recorded_search_is_bounded_and_short_page():
    cov, rows = FIG.search(replay("search-core"), CORE_MYELOMA)
    assert cov.complete == "short-page" and cov.total is None and cov.returned == len(rows)
    assert all(CORE_MYELOMA.since.isoformat() <= r.date <= CORE_MYELOMA.until.isoformat() for r in rows)


def test_recorded_supplement_keeps_both_dois_apart_and_lists_its_table(monkeypatch):     # spec §12's pitfall test
    shown = FIG.show(replay("show-supplement"), SUPPLEMENT)
    assert shown.publication == "doi:10.1371/journal.pone.0360063"
    assert "doi:10.1371/journal.pone.0360063.t007" in shown.xrefs
    _, files = FIG.files(replay("files-supplement"), SUPPLEMENT)
    assert [f.name for f in files] == ["Table 7.xls"] and files[0].media_type == "application/vnd.ms-excel"
    monkeypatch.setattr(context, "make_client", lambda trace: replay("files-supplement"))
    out = CliRunner().invoke(main, ["--json", "files", "figshare:34254439", "--acquisition"], catch_exceptions=False)
    assert out.exit_code == 0 and json.loads(out.stdout)["locator"] == "accession:figshare:34254439"
```

Run: `just test-one tests/test_figshare.py`
Expected: FAIL at import (`cannot import name 'figshare'`).

- [ ] **Step 3: Write the adapter**

`src/quarry/adapters/figshare.py`:

```python
"""figshare (spec §13): dataset items through the POSTed search. No total is reported, so
paging ends on a short page; the search's date parameters are not applied by the service, so
quarry orders by published_date and bounds the listing itself."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date

from quarry.catalog import Resource
from quarry.errors import QuarryError
from quarry.files import File
from quarry.http import Client, UpstreamError
from quarry.ids import Curie
from quarry.query import Query
from quarry.results import Coverage, Result
from quarry.search import completeness

CAP = 100
TAGS = re.compile(r"<[^>]+>")
ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})")
STRINGS = ("title", "doi", "resource_doi", "description")


def _quote(text: str) -> str:
    if '"' in text:
        raise QuarryError("unsupported_filter", f"a figshare phrase cannot contain a double quote: {text!r}; use --native")
    return f'"{text}"'


def compile_query(query: Query) -> str:
    """The search_for string; dates are not in it (they are applied through ordering and bounds)."""
    return query.native if query.native is not None else " OR ".join(_quote(p) for p in query.phrases)


def _json(text: str, what: str):
    try:
        return json.loads(text)
    except ValueError:
        raise UpstreamError(f"figshare {what}: not JSON") from None


def _valid(h: dict) -> dict:
    if type(h.get("id")) is not int:
        raise UpstreamError("figshare item: id is not a number")
    for name in STRINGS:
        if h.get(name) is not None and type(h[name]) is not str:
            raise UpstreamError(f"figshare item: {name} is not text")
    if type(h.get("published_date")) is not str or not ISO.match(h["published_date"]):
        raise UpstreamError("figshare item: published_date is not an ISO date")
    if h.get("files") is not None and (type(h["files"]) is not list or any(type(f) is not dict for f in h["files"])):
        raise UpstreamError("figshare item: files is not a list")
    return h


def _row(h: dict) -> Result:
    h = _valid(h)
    return Result(id=f"figshare:{h['id']}", resource="figshare", kind="dataset",
                  title=TAGS.sub("", h.get("title") or "").strip(), date=ISO.match(h["published_date"]).group(1),
                  summary=TAGS.sub("", h.get("description") or "").strip() or None,
                  xrefs=(f"doi:{h['doi']}",) if h.get("doi") else (),
                  publication=f"doi:{h['resource_doi']}" if h.get("resource_doi") else None)


def _files(h: dict) -> list[File]:
    files = []
    for f in h.get("files") or []:
        if type(f.get("name")) is not str or not f["name"]:
            raise UpstreamError("figshare files: a file has no name")
        if f.get("size") is not None and (type(f["size"]) is not int or f["size"] < 0):
            raise UpstreamError("figshare files: a size is not a number")
        url = f.get("download_url")
        urls = (url,) if not f.get("is_link_only") and type(url) is str and url.startswith("https://") else ()
        md5 = f.get("computed_md5")
        files.append(File(name=f["name"], urls=urls, size=f.get("size"),
                          checksums={"md5": md5} if type(md5) is str and md5 else {},
                          media_type=f["mimetype"] if type(f.get("mimetype")) is str and f["mimetype"] else None))
    return files


class Figshare:
    match = "exact"
    filters = frozenset()

    def __init__(self, resource: Resource):
        self.resource = resource
        self.route = resource.routes[0]

    def search(self, client: Client, query: Query) -> tuple[Coverage, list[Result]]:
        return self.prepare(query)(client)

    def prepare(self, query: Query) -> Callable[[Client], tuple[Coverage, list[Result]]]:
        q = compile_query(query)
        newest_first = not (query.until and not query.since)

        def execute(client: Client) -> tuple[Coverage, list[Result]]:
            size = min(CAP, query.limit) if query.limit else CAP
            rows, page, stopped = [], 1, False
            while True:
                body = {"search_for": q, "item_type": 3, "page_size": size, "page": page,
                        "order": "published_date", "order_direction": "desc" if newest_first else "asc"}
                items = _json(client.request(self.route, "POST", "articles/search", json=body).text, "search")
                if type(items) is not list or any(type(h) is not dict for h in items):
                    raise UpstreamError("figshare search: expected a list of items")
                for h in items:
                    result = _row(h)
                    when = date.fromisoformat(result.date)
                    if newest_first:
                        if query.until and when > query.until:
                            continue
                        if query.since and when < query.since:
                            stopped = True
                            break
                    elif when > query.until:
                        stopped = True
                        break
                    rows.append(result)
                short = len(items) < size
                if stopped or short or (query.limit and len(rows) >= query.limit):
                    break
                page += 1
            rows = rows[:query.limit] if query.limit else rows
            complete = completeness(total=None, returned=len(rows), limit=query.limit, short_page=stopped or short)
            match = "native" if query.native is not None else self.match
            return Coverage(self.resource.id, q, match, None, len(rows), complete), rows

        return execute

    def _article(self, client: Client, curie: Curie) -> dict:
        response = client.request(self.route, "GET", f"articles/{curie.local}", accept=frozenset({404}))
        if response.status_code == 404:
            raise QuarryError("not_found", f"{curie} is not in figshare")
        doc = _json(response.text, "article")
        if type(doc) is not dict:
            raise UpstreamError("figshare article: expected a JSON object")
        return doc

    def show(self, client: Client, curie: Curie) -> Result:
        return _row(self._article(client, curie))

    def files(self, client: Client, curie: Curie) -> tuple[Result, list[File]]:
        doc = self._article(client, curie)
        return _row(doc), _files(doc)


def make(resource: Resource) -> Figshare:
    return Figshare(resource)
```

Run: `just test-one tests/test_figshare.py -k "not recorded"`
Expected: PASS.

- [ ] **Step 4: Confirm the landing URL, record, replay, run the suite, commit**

```bash
curl -sI https://figshare.com/articles/dataset/_/34254439 | head -1      # expect 200 or a 30x to the item page
just record figshare
ls -la tests/fixtures/figshare/
just test-one tests/test_figshare.py
uv run quarry check
just test-fast
git add src/quarry tests
git commit -m "feat(figshare): items with their paper apart, client-side date bounds, files with md5"
```

If the landing URL answers 404, change `uri_format` in `figshare.toml` to the form that
resolves and say so in the commit. If `search-core.json` exceeds about ten pages (1,000
items), the month held more supplements than expected: note the count on the task and keep
the recording; the test is count-agnostic.

### Task 8: The domains' search sets, the docs, and the cross-resource proofs

**Files:**
- Create: `tests/test_domains.py`
- Modify: `src/quarry/catalog/domains/core.toml`, `src/quarry/catalog/domains/biology.toml`, `tests/test_ena.py` (one test's arguments), `tests/test_cli_surface.py` (the search invocation), `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: every adapter from Tasks 2 to 7 and both `search-core` recordings.
- Produces: `core.search_default = ["zenodo", "figshare"]`, `biology.search_default = ["geo", "ena", "biostudies"]` (spec §6), so a biology search covers GEO, ENA, BioStudies, Zenodo and figshare.

- [ ] **Step 1: Write the failing domain tests**

`tests/test_domains.py`:

```python
"""The first version's domains (spec §6, §13): what each searches by default, how bare ids
resolve now that several numeric prefixes are owned, and one core search across recordings."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from quarry import catalog as catalog_mod
from quarry import context
from quarry.cli import main
from quarry.testing import ReplayTransport, replay_client
from tests.recordings.common import CORE_MYELOMA

FX = Path(__file__).parent / "fixtures"
CAT = catalog_mod.load()


def invoke(*args):
    return CliRunner().invoke(main, ["--json", *args], catch_exceptions=False)


def test_search_sets_and_effective_resources():
    assert CAT.search_set("core") == ["zenodo", "figshare"]
    assert CAT.search_set("biology") == ["geo", "ena", "biostudies", "zenodo", "figshare"]
    assert CAT.effective_resources("biology") == ["geo", "ena", "biostudies", "zenodo", "figshare", "pubmed"]
    assert {rid: CAT.resources[rid].roles for rid in CAT.resources} == {
        "biostudies": ("repository",), "ena": ("repository",), "figshare": ("repository",), "geo": ("repository",),
        "pubmed": ("literature-index",), "zenodo": ("repository",)}


def test_domains_list_shows_both_domains_with_their_resources():
    out = invoke("domains", "list")
    rows = {d["id"]: d for d in json.loads(out.stdout)["domains"]}
    assert rows["biology"]["resources"] == ["geo", "ena", "biostudies", "zenodo", "figshare", "pubmed"]
    assert rows["core"]["resources"] == ["zenodo", "figshare", "pubmed"] and rows["core"]["beliefs_contract"] is None


def test_check_is_clean_with_every_shipped_adapter():
    out = CliRunner().invoke(main, ["check"], catch_exceptions=False)
    assert out.exit_code == 0 and out.stdout == ""


@pytest.mark.parametrize("bare, expected", [
    ("GSE24080", "geo:GSE24080"), ("E-GEOD-5900", "arrayexpress:E-GEOD-5900"), ("S-BSST1234", "biostudies:S-BSST1234"),
    ("PRJNA130207", "bioproject:PRJNA130207"), ("SRP000001", "insdc.sra:SRP000001"),
])
def test_bare_accessions_with_one_owner_normalize(bare, expected):
    assert json.loads(invoke("id", "normalize", bare).stdout) == {"id": expected}


def test_bare_numbers_are_ambiguous_among_three_owners():               # Review Focus 4
    out = invoke("id", "normalize", "12345")
    error = json.loads(out.stderr)["error"]
    assert out.exit_code == 1 and error["kind"] == "ambiguous_id"
    assert all(p in error["detail"] for p in ("figshare", "pubmed", "zenodo.record"))
    out = invoke("show", "12345")
    assert out.exit_code == 1 and json.loads(out.stderr)["error"]["kind"] == "ambiguous_id"


def test_core_search_replays_zenodo_then_figshare(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport.from_files(
        [FX / "zenodo" / "search-core.json", FX / "figshare" / "search-core.json"])))
    out = invoke("search", "--domain", "core", "--since", CORE_MYELOMA.since.isoformat(),
                 "--until", CORE_MYELOMA.until.isoformat(), *CORE_MYELOMA.phrases)
    assert out.exit_code == 0, out.stderr
    report = json.loads(out.stdout)
    assert report["domain"] == "core"
    assert [c["resource"] for c in report["coverage"]] == ["zenodo", "figshare"]
    assert [c["complete"] for c in report["coverage"]] == ["total", "short-page"]
    assert [c["match"] for c in report["coverage"]] == ["exact", "exact"]
    assert all(r["resource"] in ("zenodo", "figshare") for r in report["results"])


def test_exact_biology_search_names_biostudies_before_any_request(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))   # any request would fail
    out = invoke("search", "--domain", "biology", "--exact", "x")
    assert out.exit_code == 1 and "biostudies" in json.loads(out.stderr)["error"]["detail"]


def test_taxon_curie_in_biology_names_biostudies_before_any_request(monkeypatch):
    monkeypatch.setattr(context, "make_client", lambda trace: replay_client(ReplayTransport([])))   # any request would fail
    out = invoke("search", "--domain", "biology", "--organism", "ncbitaxon:9606", "x")
    assert out.exit_code == 1 and "biostudies" in json.loads(out.stderr)["error"]["detail"]
```

Run: `just test-one tests/test_domains.py`
Expected: FAIL on the search sets (`["zenodo", "figshare"] != []`).

- [ ] **Step 2: Set the search defaults and move two tests to `--in ena`**

`src/quarry/catalog/domains/core.toml`:

```toml
id = "core"
summary = "Resources every science shares: general repositories, literature, identifiers"
resources = ["zenodo", "figshare", "pubmed"]
search_default = ["zenodo", "figshare"]
```

`src/quarry/catalog/domains/biology.toml`:

```toml
id = "biology"
summary = "Life-science repositories, knowledgebases, identifiers and ontologies"
includes = ["core"]
resources = ["geo", "ena", "biostudies"]
search_default = ["geo", "ena", "biostudies"]
filters = ["organism"]
beliefs_contract = "biology"           # the contract science activates by this name
beliefs_contract_version = 1           # the version this domain was written against

[facet_namespaces]                     # the contract's facet namespaces, as Bioregistry prefixes
HGNC = "hgnc"
```

Two tests replay ENA fixtures alone under `--domain biology`, which now covers five resources:
in `tests/test_ena.py::test_cli_search_through_replay` change `["--json", "search", "--domain", "biology"]`
to `["--json", "search", "--in", "ena"]`, and in `tests/test_cli_surface.py::test_json_is_honoured_by_every_leaf_command`
change `search_args = ["search", "--domain", "biology"]` to `search_args = ["search", "--in", "ena"]`.

Run: `just test-one tests/test_domains.py tests/test_ena.py tests/test_cli_surface.py`
Expected: PASS.

- [ ] **Step 3: Update the guides**

In `README.md`, replace the Usage block and the two paragraphs after it with:

````markdown
```sh
uv run quarry --version
uv run quarry --help
uv run quarry help
uv run quarry resources list --domain biology
uv run quarry search --domain biology --organism "Homo sapiens" "multiple myeloma"
uv run quarry search --in pubmed --since 2024-01-01 "smoldering myeloma"
uv run quarry show geo:GSE24080
uv run quarry files geo:GSE24080
uv run quarry files zenodo.record:21628233 --file "*.zip" --acquisition
```

The root command accepts `--json` and `--pretty` to choose output mode.
`QUARRY_FORMAT` sets the default to `json` or `pretty`.

The first version's repositories are GEO, ENA and BioStudies (biology) and Zenodo and
figshare (core), with PubMed as a literature index named with `--in`. A biology search
covers all five repositories. `files` lists a record's files with every location the
resource gives; `files --acquisition` prints the request the kernel's acquisition takes
(title, `accession:<CURIE>` locator, one http(s) URL per file). Ontology terms, agent
skills and live checks are the next milestone.
````

In `README.md`'s Layout, change the adapters line to
``- `src/quarry/adapters/`: one module per resource, plus `eutils.py` shared by GEO and PubMed.``
and the recordings line to
``- `tests/recordings/`: live recording scenarios, refreshed with `just record <resource>`.``

In `AGENTS.md`'s Layout, change the adapters line to
``- `src/quarry/adapters/`: one module per supported resource; `eutils.py` is shared by GEO and PubMed.``

```bash
python3 tools/ops-docs check
```

Expected: no output (the identity regions are untouched).

- [ ] **Step 4: Run the suite and the check, commit**

```bash
just test-fast
just check
git add src/quarry tests README.md AGENTS.md
git commit -m "feat(domains): biology searches GEO, ENA and BioStudies; core searches Zenodo and figshare"
```

## Execution notes

- Order: Task 1 before Task 2 (the table row); Task 3 before Task 4 (the helper); Task 6
  before Task 7 (`tests/recordings/common.py`); Task 8 last. Tasks 3 to 7 add their resource
  to a domain's `resources` only; Task 8 sets `search_default`, so the ENA-only replays keep
  passing until then.
- Recording needs the network and is a deliberate step inside Tasks 2 to 7. `NCBI_API_KEY`
  and `ZENODO_TOKEN` may be set or unset; a set key is redacted in the fixtures and ignored
  by replay. A fixture that comes back empty or implausibly large is noted on the task before
  the scenario is changed.
- Every task's commit is preceded by `just test-fast`; the pre-push hook runs the full gate.
- `quarry-e0f1c0` (required-key refusal) stays separate: no route in this plan is `required-key`.
- When the plan lands, the consuming tasks `sci-a17882` (science acquire) and `mm-13aac8`
  (roster migration) can start; both depend on `quarry-3f4c3b`.
