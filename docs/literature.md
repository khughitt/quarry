# Literature indexes

Type: guide, data model; document types 1. Publication metadata and availability
fields are currently unversioned. This covers PubMed and Europe PMC (with
eligible hosted PMC full-text locators) as well as NASA ADS and arXiv
(metadata-only search and lookup). The [CLI guide](cli.md) covers shared
commands, and the [JSON model](output.md) defines record, coverage and file
envelopes.

## Find a publication and select a representation

```sh
uv run quarry search --in europepmc --since 2024-01-01 -n 10 "bioinformatics software"
uv run quarry show europepmc:MED/22253597
uv run quarry show pmc:PMC3257301
uv run quarry show ppr:PPR404258
uv run quarry show pubmed:29401521
uv run quarry files pubmed:29401521 --file "*.xml" --acquisition
uv run quarry files pubmed:29401521 --file "*.bioc.json" --acquisition
```

PubMed and Europe PMC stay outside default search sets. Europe PMC searches
the whole index and preserves source/id pairs as its primary IDs, so search
results round-trip through that reporter. Mapped source identifiers remain
cross-references.

| Europe PMC source | Canonical cross-reference prefix |
| --- | --- |
| MED | pubmed |
| PMC | pmc |
| PPR | ppr |
| AGR | agricola |
| CBA | cba |
| CTX | citexplore |

The pmc and ppr aliases use Europe PMC and return its provider-qualified ID.
Version-specific PMC lookup inputs are refused. Pubmed lookups use E-utilities
esummary plus PMC OAI-PMH GetRecord with metadataPrefix pmc_fm when an
unversioned PMC cross-reference exists.

Phrase queries disable Europe PMC synonym expansion, combine phrases with OR
and inclusive first-publication dates with AND, and report match expanded.
PubMed also reports expanded matching because of automatic term mapping.
These indexes refuse exact and organism filtering. Unrepresentable literal
phrase syntax points to native queries. Native queries pass unchanged and
the result limit still applies. Count/cursor inconsistencies fail.
Invalid mapped IDs cause upstream_failed. In a multi-resource record search,
allow-partial can retain other resources while recording failed coverage.

## Availability fields

These optional string fields occur in the record's domain_fields.core object.
The record resource identifies the reporter.

| Key | Europe PMC source | PubMed source | Meaning |
| --- | --- | --- | --- |
| open_access | isOpenAccess | pmc-prop-open-access, yes mapped to Y and no to N | Reporter's OA designation. |
| in_epmc | inEPMC | pmc-prop-in-epmc, yes mapped to Y and no to N | Full text present in Europe PMC. |
| in_pmc | inPMC | esummary PMC cross-reference and pmc_fm | Full text present in PMC. |
| license | license | pmc-license-ref | Reporter's exact licence text. |

Availability flags retain Y/N spelling. Licence text is preserved without
conversion to SPDX identifiers. Missing or null upstream values are omitted
and mean unknown. Malformed values fail. Definitions from different reporters
are not assumed equivalent.

Europe PMC search uses available lite metadata. Show/files use core metadata
for licences. PubMed search derives only in_pmc, Y or N, from esummary without
per-hit OAI requests. Show/files read pmc_fm for unversioned PMC cross-references,
using a 0.34-second per-process service interval and sequential requests.

With no PMC cross-reference, or an OAI idDoesNotExist response, PubMed sets
in_pmc to N and omits open_access, in_epmc and license. A valid versioned PMC
cross-reference is retained in xrefs with in_pmc Y, while OAI lookup and file
locators are withheld. Pretty show labels known availability values, and
ordinary file listings can carry license.

Archive presence describes a location. OA is the provider's designation.
Use the reported licence and the consuming application's policy to decide
retrieval and exposure.

## Eligible files

Europe PMC lists a PMCID.xml file through its fullTextXML route only when
open_access is Y, in_epmc is Y and the PMCID is unversioned and valid.

PubMed lists PMCID.xml through E-utilities efetch with db pmc and retmode xml,
with media type application/xml, and PMCID.bioc.json through the PubTator3
pmc_export/biocjson route, with media type application/json. All of the
following pmc_fm conditions must hold.

- open_access is Y, from pmc-prop-open-access yes.
- in_pmc is Y and the PMCID is valid and unversioned.
- pmc-status-embargo is no.
- pmc-prop-pdf-only is no.

Non-OA author manuscripts in OAI set pmc-open remain ineligible when their
pmc-prop-open-access value is no. Eligibility is derived from metadata, with
no probe of article bytes. PubTator3 refuses non-OA records with HTTP 400.

An empty file inventory means that these eligibility rules selected no route.
Inventory coverage stops at the hosted PMC representations described above.
Other copies can include preprint full text, publisher PDFs, supplements and books.
Metadata eligibility cannot guarantee provider uptime or that PubTator3's
weekly updates have indexed a newly released OA article.

Acquisition requests retain their shared schema, with licence metadata in the
ordinary file listing. Select XML or BioC JSON with a file glob. Empty selections
raise no_files. Abstracts are absent from quarry's output even when upstream
core responses or replay fixtures contain them. The consuming application
controls byte retrieval, section isolation and display policy.

## NASA ADS and arXiv

```sh
ADS_DEV_KEY=<token> uv run quarry search --in ads --since 2012-01-01 --until 2012-12-31 -n 5 "Atmospheric Imaging Assembly"
ADS_DEV_KEY=<token> uv run quarry show bibcode:2012SoPh..275...17L
uv run quarry search --in arxiv --since 2011-01-01 --until 2022-12-31 -n 5 "Atmospheric Imaging Assembly"
uv run quarry search --in arxiv --native 'cat:cs.SE AND ti:"software testing"' -n 5
uv run quarry show arxiv:2108.02624v2
uv run quarry show arxiv:astro-ph/0601001
```

Both `ads` and `arxiv` stay outside default search sets, require phrases on
ordinary searches, carry no observation-time filters (`--during` and `--at` are
refused before transport), and report `match: expanded` (refusing `--exact`).
Both are metadata-only: `files` returns the record with an empty file inventory,
there is no PDF acquisition workflow, and abstracts are omitted from quarry's
output.

### NASA ADS (`ads`)

ADS owns the canonical `bibcode` prefix and requires a Bearer token in the
`ADS_DEV_KEY` environment variable (`Authorization: Bearer <token>`). Calls
without `ADS_DEV_KEY` refuse before transport with `missing_required_key`, and
credentials are never echoed into `Coverage.native`, traces or error details.
Cross-references (`doi:`, `arxiv:`) are validated through `refs` and emitted in
`xrefs`.

ADS `pubdate` values carry year (`YYYY-00-00`), month (`YYYY-MM-00`) or day
(`YYYY-MM-DD`) precision. Quarry emits `date` at its known precision (`YYYY`,
`YYYY-MM` or `YYYY-MM-DD`) without fabricating missing day or month fields.
Because ADS `pubdate` indexing operates at month granularity, ordinary
`--since`/`--until` filters require month-aligned bounds (`--since` on the first
day of a month, `--until` on the last day of a month) and refuse sub-month
bounds before transport, pointing to `--native` `pubdate:[...]` syntax. When a
date-bounded ordinary search returns a record whose `pubdate` is missing or too
coarse to establish strict membership in `[since, until]` (for example, a
year-only `2012-00-00` record in a sub-year window), the search fails with
`upstream_failed` rather than letting an ambiguous coarse date slip through.
Stable offset paging checks `numFound` consistency and distinct bibcodes across
pages.

### arXiv (`arxiv`)

Ordinary `--in arxiv` searches apply `cat:astro-ph.SR` across every lens
(`core`, `biology` and `heliophysics`). The selector appears in the coverage
row's `native` query and triggers a coverage warning naming `cat:astro-ph.SR`
and how to override it. Passing `--native` sends the caller's `search_query`
unchanged without injecting `cat:astro-ph.SR` and without emitting the
default-category warning.

`arxiv` sets `bare_normalize = false` in the catalog because arXiv's upstream
Bioregistry pattern also matches bare accessions owned by other resources (such
as GEO, PMC, PPR, BioProject and SRA). Supply qualified `arxiv:<id>` CURIEs to
`id normalize`, `show` and `files`. Modern versioned (`arxiv:2108.02624v2`) and
unversioned (`arxiv:2108.02624`) identifiers as well as legacy identifiers
supported by `refs` (`arxiv:astro-ph/0601001`) are handled; when Atom returns a
version suffix on a legacy identifier that the pinned `refs` pattern omits, the
canonical unversioned CURIE is emitted with the version recorded in
`domain_fields.core.version`.

Release-date bounds (`--since`/`--until`) compile to
`submittedDate:[YYYYMMDD0000 TO YYYYMMDD2359]` (using `199101010000` or
`299912312359` for open ends) with URL-encoded range brackets, separate from the
default category clause. Atom `<entry>` records are parsed separately from the
feed-level query-echo `<title>`, per-record licence metadata is preserved in
`domain_fields.core.license` when present, requests respect arXiv's 3-second
politeness interval, and malformed XML or `opensearch:totalResults`/paging
inconsistencies fail.
