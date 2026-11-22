"""Live checks: `just live` makes the smallest real request on every catalog route and
reports each one. Opt-in and never part of a test run; it reports, it does not gate."""
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from quarry import catalog as catalog_mod, ids
from quarry.adapters import base
from quarry.errors import QuarryError
from quarry.http import Client
from quarry.ids import Curie
from quarry.query import Query, Window, parse_instant

Probe = Callable[[object, Client], None]

REGISTRY = ids.registry_for(catalog_mod.load())
SMOLDERING = Query(phrases=("smoldering myeloma",), limit=1)
MYELOMA_SINCE = Query(phrases=("multiple myeloma",), since=date(2024, 1, 1), limit=1)

ASSIGNMENT_RE = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})-epoch-assignment-([0-9]+)\.md$")
VSO_WINDOW = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                    datetime(2025, 1, 15, 0, 1, 0, tzinfo=timezone.utc))
HEK_WINDOW = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                    datetime(2025, 1, 15, 0, 30, 0, tzinfo=timezone.utc))
JSOC_WINDOW = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 15, 0, 0, 36, tzinfo=timezone.utc))
SOAR_WINDOW = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 15, 0, 5, 0, tzinfo=timezone.utc))
SOHO_WINDOW = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
STEREO_WINDOW = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                       datetime(2025, 1, 15, 0, 15, 0, tzinfo=timezone.utc))
GOES_WINDOW = Window(datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
                     datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc))
HV_INSTANT = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
JSOC_SAMPLE_CURIE = Curie("jsoc", "aia.lev1_euv_12s[2025-01-15T00:00:11Z][193]")
SOAR_SAMPLE_CURIE = Curie("soar", "solo_L1_eui-fsi304-image-short_20250115T000015208")


def _resolve_sun_root(sun_root: Path | None = None) -> Path:
    if sun_root is not None:
        return sun_root
    env = os.environ.get("QUARRY_SUN_ROOT", "").strip()
    if env:
        return Path(env)
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        common = Path(out)
        if not common.is_absolute():
            common = (Path(__file__).resolve().parent / common).resolve()
        else:
            common = common.resolve()
        return common.parent.parent / "sun"
    except (OSError, subprocess.SubprocessError) as exc:
        raise QuarryError("upstream_failed", f"cannot locate sun checkout for epoch verification: {exc}") from None


def _parse_exploration_epochs(text: str) -> list[tuple[datetime | None, datetime | None]]:
    in_section = False
    epochs: list[tuple[datetime | None, datetime | None]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            if in_section:
                break
            in_section = line == "## Epochs"
            continue
        if not in_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0].lower() == "epoch" or set(cells[0]) <= {"-"}:
            continue
        _, raw_from, raw_to, role = cells[:4]
        if role.lower() != "exploration":
            continue
        lower = None if raw_from.lower() == "start of each series" else parse_instant(raw_from)
        upper = None if raw_to.lower() == "open" else parse_instant(raw_to)
        epochs.append((lower, upper))
    return epochs


def require_exploration_window(start: datetime, end: datetime, *, sun_root: Path | None = None) -> Path:
    """Verify [start, end) lies strictly inside an exploration epoch in sun's latest epoch assignment."""
    if start.tzinfo is not timezone.utc or end.tzinfo is not timezone.utc or start >= end:
        raise QuarryError("upstream_failed", "probe window must be a non-empty UTC interval [start, end)")
    root = _resolve_sun_root(sun_root)
    notes_dir = root / "docs" / "notes"
    if not notes_dir.is_dir():
        raise QuarryError("upstream_failed", f"sun epoch assignment directory not found: {notes_dir}")
    candidates: list[tuple[int, str, Path]] = []
    for path in notes_dir.iterdir():
        m = ASSIGNMENT_RE.fullmatch(path.name)
        if m and path.is_file():
            candidates.append((int(m.group(2)), m.group(1), path))
    if not candidates:
        raise QuarryError("upstream_failed", f"no epoch assignment file found under {notes_dir}")
    _, _, latest = max(candidates)
    try:
        epochs = _parse_exploration_epochs(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QuarryError("upstream_failed", f"cannot parse exploration epochs from {latest.name}: {exc}") from None
    if not epochs:
        raise QuarryError("upstream_failed", f"no exploration epochs found in {latest.name}")
    for lower, upper in epochs:
        lower_ok = lower is None or start > lower
        upper_ok = upper is None or end < upper
        if lower_ok and upper_ok:
            return latest
    raise QuarryError(
        "upstream_failed",
        f"probe window {start.isoformat()}..{end.isoformat()} is not strictly inside an exploration epoch in {latest.name}",
    )


def search_one(query: Query) -> Probe:
    def probe(adapter, client):
        _, rows = adapter.search(client, query)
        if not rows:
            raise QuarryError("upstream_failed", "the search answered no results")
    return probe


def dated_search_one(query: Query) -> Probe:
    assert query.during is not None
    window = query.during

    def probe(adapter, client):
        require_exploration_window(window.start, window.end)
        _, rows = adapter.search(client, query)
        if not rows:
            raise QuarryError("upstream_failed", "the search answered no results")
    return probe


def show(curie: Curie) -> Probe:
    return lambda adapter, client: adapter.show(client, curie)


def files(curie: Curie) -> Probe:
    return lambda adapter, client: adapter.files(client, curie)


def local_tool(module_name: str) -> Probe:
    def probe(adapter, client):
        try:
            spec = importlib.util.find_spec(module_name)
        except ModuleNotFoundError:
            spec = None
        if spec is None:
            raise QuarryError("upstream_failed", f"optional local tool {module_name!r} is not installed")
        importlib.import_module(module_name)
    return probe


def cdaweb_hapi(adapter, client: Client) -> None:
    route = next(r for r in adapter.resource.routes if r.url == "https://cdaweb.gsfc.nasa.gov/hapi")
    cat = json.loads(client.request(route, "GET", "catalog").text)
    if type(cat) is not dict or not cat.get("catalog"):
        raise QuarryError("upstream_failed", "CDAWeb HAPI /catalog returned no datasets")
    info = json.loads(client.request(route, "GET", "info", params={"id": "OMNI_HRO2_1MIN"}).text)
    if type(info) is not dict or not info.get("parameters"):
        raise QuarryError("upstream_failed", "CDAWeb HAPI /info returned no parameters")


def cdaweb_bulk(adapter, client: Client) -> None:
    route = next(r for r in adapter.resource.routes if r.url == "https://cdaweb.gsfc.nasa.gov/sp_phys/data")
    client.request(route, "HEAD", f"{route.url}/")


def jsoc_export_status(adapter, client: Client) -> None:
    """Non-submitting op=exp_status metadata probe; refuses via MissingKeyError when JSOC_NOTIFY_EMAIL is absent."""
    adapter.export_status(client, "JSOC_20000101_000")


def jsoc_sums_head(adapter, client: Client) -> None:
    """Metadata-only HEAD on one online X-25 SUMS segment (no image bytes)."""
    require_exploration_window(JSOC_WINDOW.start, JSOC_WINDOW.end)
    _, seg_files = adapter.files(client, JSOC_SAMPLE_CURIE)
    if not seg_files or not seg_files[0].urls:
        raise QuarryError("upstream_failed", "JSOC sample record has no online SUMS segment URL")
    client.request(adapter.sums_route, "HEAD", seg_files[0].urls[0])


def soar_download_metadata(adapter, client: Client) -> None:
    """Metadata-only HEAD on one public X-25 SOAR item download URL (no file bytes)."""
    require_exploration_window(SOAR_WINDOW.start, SOAR_WINDOW.end)
    _, item_files = adapter.files(client, SOAR_SAMPLE_CURIE)
    if not item_files or not item_files[0].urls:
        raise QuarryError("upstream_failed", "SOAR sample public item returned no download URL")
    resp = client.request(adapter.data_route, "HEAD", item_files[0].urls[0])
    if not resp.headers.get("content-length"):
        raise QuarryError("upstream_failed", "SOAR download HEAD returned no Content-Length")


def helioviewer_nearest(adapter, client: Client) -> None:
    """Metadata-only getDataSources + getClosestImage probe inside X-25 (no image bytes)."""
    require_exploration_window(HV_INSTANT, HV_INSTANT + timedelta(minutes=1))
    _, rows = adapter.search(client, Query(at=HV_INSTANT, filters={"instrument": "AIA 335"}))
    if not rows:
        raise QuarryError("upstream_failed", "Helioviewer nearest search answered no image")


def goes_swpc_rolling_head(adapter, client: Client) -> None:
    """HEAD-only metadata probe on SWPC rolling GOES JSON (zero body bytes read under sun controls)."""
    resp = client.request(adapter.swpc_route, "HEAD", "xrays-6-hour.json")
    ct = (resp.headers.get("content-type") or "").lower()
    if "json" not in ct:
        raise QuarryError("upstream_failed", f"SWPC GOES rolling HEAD returned unexpected content-type {ct!r}")


def terms(curie: Curie, words: str) -> Probe:
    """One OLS route's three paths: the term (through ancestors), its ancestors, and search."""
    def probe(adapter, client):
        adapter.ancestors(client, REGISTRY, curie)
        _, found, _, _ = adapter.search(client, REGISTRY, words, exact=False, limit=1)
        if not found:
            raise QuarryError("upstream_failed", "the term search answered no terms")
    return probe


PROBES: dict[tuple[str, str], Probe] = {
    ("geo", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"): search_one(MYELOMA_SINCE),
    ("geo", "https://www.ncbi.nlm.nih.gov/geo/query"): files(Curie("geo", "GSE24080")),
    ("ena", "https://www.ebi.ac.uk/ena/portal/api"): search_one(MYELOMA_SINCE),
    # the taxonomy service answers an organism name before the portal search runs
    ("ena", "https://www.ebi.ac.uk/ena/taxonomy/rest"):
        search_one(Query(phrases=("myeloma",), filters={"organism": "Mus musculus"}, limit=1)),
    ("biostudies", "https://www.ebi.ac.uk/biostudies/api/v1"): search_one(SMOLDERING),
    ("zenodo", "https://zenodo.org/api"): search_one(MYELOMA_SINCE),
    ("figshare", "https://api.figshare.com/v2"): search_one(MYELOMA_SINCE),
    ("pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"): search_one(SMOLDERING),
    ("pubmed", "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh"): files(Curie("pubmed", "29401521")),
    ("europepmc", "https://www.ebi.ac.uk/europepmc/webservices/rest"): search_one(SMOLDERING),
    ("go", "https://www.ebi.ac.uk/ols4/api/ontologies/go"): terms(Curie("go", "0006915"), "apoptotic process"),
    ("uberon", "https://www.ebi.ac.uk/ols4/api/ontologies/uberon"): terms(Curie("uberon", "0000948"), "heart"),
    ("ncbitaxon", "https://www.ebi.ac.uk/ols4/api/ontologies/ncbitaxon"): terms(Curie("ncbitaxon", "9606"), "human"),
    ("vso", "http://vso.nso.edu/cgi-bin/VSO/PROD/vsoi_wsdl.cgi"):
        dated_search_one(Query(during=VSO_WINDOW, filters={"instrument": "AIA", "observatory": "SDO"}, limit=1)),
    ("vso", "sunpy.net.vso"): local_tool("sunpy.net.vso"),
    ("cdaweb", "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys"): show(Curie("cdaweb", "OMNI_HRO2_1MIN")),
    ("cdaweb", "https://cdaweb.gsfc.nasa.gov/hapi"): cdaweb_hapi,
    ("cdaweb", "https://cdaweb.gsfc.nasa.gov/sp_phys/data"): cdaweb_bulk,
    ("cdaweb", "sunpy.net.cdaweb"): local_tool("sunpy.net.cdaweb"),
    ("hek", "https://www.lmsal.com/hek/her"):
        dated_search_one(Query(during=HEK_WINDOW, filters={"instrument": "HMI"}, limit=1)),
    ("hek", "sunpy.net.hek"): local_tool("sunpy.net.hek"),
    ("jsoc", "http://jsoc.stanford.edu/cgi-bin/ajax"):
        dated_search_one(Query(phrases=("aia.lev1_euv_12s",), during=JSOC_WINDOW, limit=1)),
    ("jsoc", "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_fetch"): jsoc_export_status,
    ("jsoc", "http://jsoc.stanford.edu"): jsoc_sums_head,
    ("soar", "https://soar.esac.esa.int/soar-sl-tap/tap"):
        dated_search_one(Query(during=SOAR_WINDOW, filters={"instrument": "EUI"}, limit=1)),
    ("soar", "https://soar.esac.esa.int/soar-sl-tap/data"): soar_download_metadata,
    ("soar", "sunpy.net.soar"): local_tool("sunpy.net.soar"),
    ("helioviewer", "https://api.helioviewer.org/v2"): helioviewer_nearest,
    ("soho", "https://umbra.nascom.nasa.gov/pub"):
        dated_search_one(Query(during=SOHO_WINDOW, filters={"instrument": "EIT"}, limit=1)),
    ("stereo-ssc", "https://stereo-ssc.nascom.nasa.gov/data/ins_data"):
        dated_search_one(Query(during=STEREO_WINDOW, filters={"instrument": "EUVI", "observatory": "STEREO-A"}, limit=1)),
    ("goes-xrs", "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes"):
        dated_search_one(Query(during=GOES_WINDOW, filters={"observatory": "GOES-18", "instrument": "xrsf-l2-avg1m_science"}, limit=1)),
    ("goes-xrs", "https://services.swpc.noaa.gov/json/goes/primary"): goes_swpc_rolling_head,
    ("goes-xrs", "sunpy.net.dataretriever.sources.goes"): local_tool("sunpy.net.dataretriever.sources.goes"),
}


def run(catalog, client: Client, probes: dict[tuple[str, str], Probe]) -> list[tuple[str, str, str]]:
    """One (resource, route url, "ok" | "<kind>: <detail>") per route; every probe runs."""
    results = []
    for resource in catalog.resources.values():
        for route in resource.routes:
            try:
                adapter = base.make(resource)
                probes[(resource.id, route.url)](adapter, client)
                outcome = "ok"
            except QuarryError as exc:
                outcome = f"{exc.kind}: {exc.detail}"
            except Exception as exc:  # a reporting harness: one broken adapter must not hide the other routes
                outcome = f"{type(exc).__name__}: {exc}"
            results.append((resource.id, route.url, outcome))
    return results


def main() -> int:
    with Client() as client:
        results = run(catalog_mod.load(), client, PROBES)
    for resource, url, outcome in results:
        print(f"ok\t{resource}\t{url}" if outcome == "ok" else f"failed\t{resource}\t{url}\t{outcome}")
    return 1 if any(outcome != "ok" for *_, outcome in results) else 0


if __name__ == "__main__":
    sys.exit(main())
