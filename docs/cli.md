# Using quarry

Type: guide; document types 1. This covers the CLI's catalog, query,
identifier, term and file workflows. The [JSON model](output.md) defines
outputs, and the [literature guide](literature.md) covers publication-specific
metadata. [Setup](../README.md#setup) and [developer checks](../AGENTS.md#gates)
describe the checkout and maintenance routes.

## Discover resources and select a domain

Run commands through `uv run quarry` in the checkout. `just install` in the main
checkout links `bin/quarry` into `~/.local/bin`, so `quarry` runs from any directory;
the launcher executes the checkout's `.venv` entry point and names `just setup` when
that is missing.

```sh
uv run quarry domains list --json
uv run quarry resources list --domain biology --role repository
uv run quarry resources show ena --json
uv run quarry help search
```

Domain precedence is `--domain`, then `QUARRY_DOMAIN`, then the `domain`
value in a caller-created `quarry.toml`, then core. `--config` names a
specific file. Otherwise quarry searches upward from the working directory,
or the directory selected with `-C`. The domain selects defaults for search
and term search and determines available filters. Selecting a resource with
`--in` keeps that domain selection. Explicit record/term IDs select their
owning resource.

Core defaults to Zenodo and figshare. Biology adds GEO, ENA and BioStudies.
Heliophysics adds VSO, CDAWeb and HEK. Literature indexes (PubMed, Europe PMC,
NASA ADS and arXiv) are selected with `--in`. Resource inspection reports
roles, routes, service limits and pitfalls. The shipped
[core](../skills/quarry/SKILL.md#resource-notes),
[biology](../skills/quarry-biology/SKILL.md#resource-notes) and
[heliophysics](../skills/quarry-heliophysics/SKILL.md#resource-notes) notes
summarize resource-specific behavior.

## Search records and read coverage

```sh
uv run quarry search --domain biology -n 10 "multiple myeloma"
uv run quarry search --domain biology --in geo --organism ncbitaxon:9606 --since 2024-01-01 "single cell"
uv run quarry search --domain heliophysics --during 2025-01-15T00:00:00Z/2025-01-15T00:00:16Z
uv run quarry search --domain heliophysics --in helioviewer --at 2025-01-15T00:00:00Z --instrument "AIA 335"
uv run quarry search --in pubmed --native 'myeloma[Title]' -n 5 --json
```

Each positional argument is a phrase. Phrases combine with OR, and filters
and inclusive release-date bounds combine with AND. Dates accept YYYY-MM-DD,
a number of days followed by d, or a number of weeks followed by w.
A multiword phrase needs shell quoting. A search may omit phrases when it
carries a domain filter; a resource that needs phrases is then skipped or
refused like any other it cannot express. `--exact` needs phrases and
requires a resource that can preserve literal matching. `--native` requires
exactly one `--in` and replaces phrases, domain filters, exact and date
options. Its query passes through unchanged, while `-n` still bounds returned
records per resource.

Inspect each coverage row before trusting a count.

| `complete` | Interpretation |
| --- | --- |
| `total` | Returned rows equal the resource's reported total. |
| `short-page` | No exact total was available and paging ended on a short page. |
| `limited` | The requested `-n` bound stopped the listing. |
| `skipped` | A default-set resource could not express this query. Read `reason`. |
| `failed` | `--allow-partial` retained another resource's results after this resource failed. |

Coverage `match` is exact, expanded, native, or none when the search had no
phrases. A default-set resource that
cannot express a filter or matching mode is skipped with a warning. Naming
that resource explicitly with `--in` causes refusal. Nothing left to search,
duplicate paged IDs and incomplete listings fail. `--allow-partial` applies
to record search, recording resource failures in coverage and warnings.

`--during START/END` and `--at INSTANT` bound observation time, where
`--since` and `--until` bound release date. They are filters of a domain that
declares them (`heliophysics` declares `during`, `at`, `instrument`,
`observatory` and `measurement`). Each endpoint is a date (YYYY-MM-DD) or a
UTC datetime ending in Z (YYYY-MM-DDTHH:MM[:SS[.ffffff]]Z). A date END
includes its whole day; a datetime END is exclusive. `--during` returns
records whose observation overlaps the interval (`time: overlap`), running
VSO, CDAWeb and HEK on the heliophysics default set while skipping Zenodo and
figshare with reasons; a resource that can select only by start time (such as
DONKI) says `time: start` and the report warns that records begun earlier are
missed. `--at` returns the record a resource reports nearest the instant
(`time: nearest`) and currently requires naming Helioviewer with `--in` and an
exact source nickname (`--in helioviewer --at <instant> --instrument "AIA 335"`,
optionally narrowed by `--observatory`); default-set `--at` fails with
`unsupported_filter` because VSO's `near` requires `start` and `end`. The two
time flags cannot be combined, and `--native` refuses both.

`--instrument` and `--observatory` narrow archives and HEK in heliophysics
(on Helioviewer, `--instrument` requires a unique case-insensitive source
nickname). `--measurement` takes a SPASE `MeasurementType` term only where an
adapter can enforce an exact mapping; approximate-only mappings are refused
before transport with `--native` syntax hints (`<physobs>` on VSO,
`instrumentType=` on CDAWeb). `--since` and `--until` remain release-date
bounds (`jsoc`, `soar`, `ads` and `arxiv` in heliophysics).

`--organism` accepts a name or ncbitaxon CURIE in biology. GEO and ENA include
subtaxa. ENA resolves a name first and refuses absent or ambiguous matches.
BioStudies matches one exact facet label, warns that subtaxa are excluded,
and skips a CURIE query from a default set. An explicit BioStudies CURIE
filter is refused.

## Normalize identifiers and read terms

```sh
uv run quarry id check PMID:21885717 geo:GSE24080
uv run quarry id normalize GSE24080
uv run quarry id resolve geo:GSE24080
uv run quarry term search apoptotic process --in go --exact
uv run quarry term show uberon:0000948
uv run quarry term ancestors go:0006915
```

Input prefix synonyms normalize to canonical CURIEs. A bare accession is
accepted by `id normalize` only when exactly one record-holding resource
owns a matching prefix and has not opted out of bare inference
(`bare_normalize = false` on a resource requires qualified `<prefix>:<id>`
inputs while keeping explicit validation, resolution and owner routing). Term
IDs need their prefix. Term search words are ANDed. Literal term searches refuse
query syntax such as wildcards and operators. The default term limit is 10 per
resource.

Term outputs carry release information and warnings. Obsolete terms may
have a replacement or consideration list. Ancestors follow is_a and can
include imported terms. Term search fails on a resource failure.

## List files and emit an acquisition request

```sh
uv run quarry files geo:GSE24080 --json
uv run quarry files zenodo.record:21628233 --file "*.zip" --acquisition
```

Plain listings retain available locations and metadata. Repeating `--file`
widens the selection using case-sensitive file-name globs. An acquisition
request chooses HTTPS before HTTP and fails if any selected file has no
supported URL, duplicate names or an invalid URL/digest. An empty selection
raises `no_files`. Only published SHA-256 values become `expected`.
The consumer retrieves bytes and chooses retrieval bounds.

## Diagnose output and failures

`--json` and `--pretty` select output, with `QUARRY_FORMAT` providing the
default. The default is pretty. `--trace` writes redacted request traces to
stderr. Optional and required service keys are named in the resource routes,
such as NCBI_API_KEY, ZENODO_TOKEN, ADS_DEV_KEY and JSOC_NOTIFY_EMAIL.

`quarry id check` is silent on success. On validation findings, read each
`where` value as the supplied ID. Add a prefix for `not_a_curie` or
`ambiguous_id`, correct `malformed` local IDs, and use a registered prefix
for `unknown_prefix`. `unsupported_pattern` means refs cannot validate
that scheme's pattern. Use `id normalize` for a bare accession.

`quarry check` diagnoses the catalog. Its finding kinds and repairs are in
[AGENTS.md](../AGENTS.md#diagnosing-checks). JSON findings go to stdout with
exit 1, runtime errors go to stderr with exit 1, and usage errors are text
on stderr with exit 2. See the [failure formats](output.md#findings-and-errors).
