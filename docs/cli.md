# Using quarry

Type: guide; document types 1. This covers the CLI's catalog, query,
identifier, term and file workflows. The [JSON model](output.md) defines
outputs, and the [literature guide](literature.md) covers publication-specific
metadata. [Setup](../README.md#setup) and [developer checks](../AGENTS.md#gates)
describe the checkout and maintenance routes.

## Discover resources and select a domain

Run commands through `uv run quarry` in the checkout, or use the installed
`quarry` entry point in that environment.

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
and term search. Explicit record/term IDs select their owning resource.

Core defaults to Zenodo and figshare. Biology adds GEO, ENA and BioStudies.
PubMed and Europe PMC are literature indexes selected with `--in`.
Resource inspection reports roles, routes, service limits and pitfalls.
The shipped [core](../skills/quarry/SKILL.md#resource-notes) and
[biology](../skills/quarry-biology/SKILL.md#resource-notes) notes summarize
resource-specific behavior.

## Search records and read coverage

```sh
uv run quarry search --domain biology -n 10 "multiple myeloma"
uv run quarry search --in geo --organism ncbitaxon:9606 --since 2024-01-01 "single cell"
uv run quarry search --in pubmed --native 'myeloma[Title]' -n 5 --json
```

Each positional argument is a phrase. Phrases combine with OR, and filters
and inclusive release-date bounds combine with AND. Dates accept YYYY-MM-DD,
a number of days followed by d, or a number of weeks followed by w.
A multiword phrase needs shell quoting. `--exact` requires a resource that
can preserve literal matching. `--native` requires exactly one `--in` and
replaces phrases, organism, exact and date options. Its query passes through
unchanged, while `-n` still bounds returned records per resource.

Inspect each coverage row before trusting a count.

| `complete` | Interpretation |
| --- | --- |
| `total` | Returned rows equal the resource's reported total. |
| `short-page` | No exact total was available and paging ended on a short page. |
| `limited` | The requested `-n` bound stopped the listing. |
| `skipped` | A default-set resource could not express this query. Read `reason`. |
| `failed` | `--allow-partial` retained another resource's results after this resource failed. |

Coverage `match` is exact, expanded or native. A default-set resource that
cannot express a filter or matching mode is skipped with a warning. Naming
that resource explicitly with `--in` causes refusal. Nothing left to search,
duplicate paged IDs and incomplete listings fail. `--allow-partial` applies
to record search, recording resource failures in coverage and warnings.

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
owns a matching prefix. Term IDs need their prefix. Term search words are
ANDed. Literal term searches refuse query syntax such as wildcards and
operators. The default term limit is 10 per resource.

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
stderr. Optional service keys are named in the resource routes, such as
NCBI_API_KEY and ZENODO_TOKEN.

`quarry id check` is silent on success. On validation findings, read each
`where` value as the supplied ID. Add a prefix for `not_a_curie` or
`ambiguous_id`, correct `malformed` local IDs, and use a registered prefix
for `unknown_prefix`. `unsupported_pattern` means refs cannot validate
that scheme's pattern. Use `id normalize` for a bare accession.

`quarry check` diagnoses the catalog. Its finding kinds and repairs are in
[AGENTS.md](../AGENTS.md#diagnosing-checks). JSON findings go to stdout with
exit 1, runtime errors go to stderr with exit 1, and usage errors are text
on stderr with exit 2. See the [failure formats](output.md#findings-and-errors).
