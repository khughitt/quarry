# Quarry JSON output

Type: data model; document types 1. The CLI JSON formats are currently
unversioned. This describes emitted fields, omission rules and failures for
automation. The [CLI guide](cli.md) covers commands and diagnosis, and the
[literature guide](literature.md) defines publication-specific availability
fields. Catalog beliefs-contract versions and ontology release versions
describe their own data and do not version this JSON format.

## Record

`show` emits one record. Search results use the same record structure.

| Field | Meaning and allowed values |
| --- | --- |
| `id` | Canonical CURIE string for the returned record. |
| `resource` | Reporting resource ID string. |
| `kind` | dataset, record or publication. |
| `title` | Title string, possibly empty. |
| `date` | Optional provider-derived date string. Omitted when unknown. |
| `summary` | Optional summary string. Omitted when unknown. |
| `xrefs` | Always-present array of canonical cross-reference CURIE strings. |
| `publication` | Optional publication CURIE. Omitted when unknown. |
| `domain_fields` | Always-present object mapping domain names to string-valued field objects. |

Primary IDs and cross-references use canonical prefixes. Invalid primary
metadata causes `upstream_failed`. Provider-specific interpretation belongs
to the reporting adapter. Literature availability keys live under
`domain_fields.core`.

Biology metadata uses `domain_fields.biology`. Its optional `organism` is
provider organism-name text, and optional `taxon` is an ncbitaxon CURIE.
GEO and ENA can supply both. BioStudies supplies the organism label.
Unavailable biology values are omitted. When none is available, the biology
entry is omitted as well.

Heliophysics metadata uses `domain_fields.heliophysics`. Whenever a
heliophysics resource supplies observation time, its records carry ISO 8601
UTC `start` and, when the observation is a span rather than an instant,
`end`—whether or not the search carried `--during` or `--at`. `Result.date`
remains the independent release or archive date (omitted when the resource
provides none). Adapters may include additional string-valued metadata keys
under `domain_fields.heliophysics` when supplied by the provider (such as
`instrument`, `observatory`, `provider`, `fileid`, `event_type`, `source_id`,
`spase_version`, `wavelength`, `jpip_uri`, `requested_at`, `series` and
`product`). Unavailable values are omitted. When none is available, the
heliophysics entry is omitted as well.

## Search report

The report contains `domain` (selected domain ID), `results` (record array),
`coverage` (one row per selected resource) and `warnings` (string array).

## Record-search coverage

| Coverage field | Meaning and allowed values |
| --- | --- |
| `resource` | Selected resource ID string. |
| `native` | Sent provider query string, or null when query preparation failed before it was built. Absent on a skipped row. |
| `match` | exact, expanded, native, or none when the search had no phrases. |
| `time` | overlap, start or nearest: how records met `--during` or `--at`. Present only when the search carried one; absent on a skipped row. |
| `total` | Integer reported total, or null when unknown. Absent on a skipped row. |
| `returned` | Integer count. Zero on failure. Absent on a skipped row. |
| `complete` | total, short-page, limited, failed or skipped. |
| `error` | Present on failure as an object with string `kind` and `detail`. |
| `reason` | Present on skipped rows as an explanatory string. |

Skipped rows contain only resource, match, complete and reason.
A failed row keeps its `time`. Failed rows
occur with `--allow-partial`, retain a nullable native query and total,
and carry returned 0. Duplicate IDs or a listing that cannot establish
completeness cause `incomplete_listing`. A caller-specified limit can produce
limited coverage. Provider warnings are gathered at report level.

## File listing

The listing contains canonical record `id`, owning `resource` and a `files`
array. An empty array is allowed.

## File

| File field | Meaning and allowed values |
| --- | --- |
| `name` | File name string used by case-sensitive glob selection. |
| `urls` | Always-present array of location strings, including unsupported transports. |
| `size` | Optional integer byte count. Omitted when unknown. |
| `checksums` | Optional object of algorithm names to digest strings. Omitted when empty. |
| `media_type` | Optional media-type string. A listed directory can use inode/directory. |
| `license` | Optional provider licence text string. Omitted when unknown. |

Known optional fields are emitted even when their values are empty strings
or zero. Listing URLs alone does not establish retrieval permission or uptime.

## Acquisition request

The request contains `title` (record title, falling back to its CURIE),
`locator` (accession: followed by the canonical record CURIE) and
`resources` (a nonempty array of selected resources).

## Acquisition resource

| Selected resource field | Meaning and allowed values |
| --- | --- |
| `name` | Unique file name string within this request. |
| `url` | One validated HTTP(S) location string, preferring HTTPS. |
| `expected` | Optional sha256: followed by 64 lowercase hexadecimal digits. |

Empty selections fail with `no_files` and repeated names with
`duplicate_file_name`. URL validation refuses userinfo, fragments, missing
hosts, whitespace/control/non-ASCII characters, malformed path escapes,
invalid ports, port zero and invalid bracketed hosts. A selected file without
a suitable URL causes `unsupported_transport`. A malformed published
SHA-256 causes `upstream_failed`. Other checksum algorithms are omitted
from the request. Licence metadata remains in the ordinary file listing.

## Term

| Term field | Meaning and allowed values |
| --- | --- |
| `id`, `label`, `resource` | Required canonical CURIE, label text and reporting resource ID. |
| `definition` | Optional text, omitted when unknown. |
| `synonyms` | Optional nonempty array of synonym objects. |
| `xrefs` | Optional nonempty canonical CURIE array. |
| `obsolete` | Present only when true. Absence means false. |
| `replaced_by` | Optional replacement CURIE, omitted when unknown. |
| `consider` | Optional nonempty alternative CURIE array. |

Unusable primary IDs fail. Unusable optional cross-references are omitted
with warnings. Search can return reduced terms without definitions.

## Synonym

A synonym has `text` (string) and `scope` (exact, related, narrow or broad).

## Release

A release always has `resource` (ID string), `version` (provider version
string or null) and `loaded` (provider load-time string or null).
OLS may derive a missing version from a date in its version IRI.

## Term show

Term show contains `term`, `release` and `warnings` (string array).

## Term ancestors

Ancestors contains a reduced `term` with id and label, `relation` equal to
is_a, an `ancestors` array of id/label pairs, `release` and `warnings`.
The output has no domain field. Imported ontology terms can occur.

## Term search

Term search contains `domain`, `terms` (term array), `coverage` and
`warnings`.

## Term coverage

Each term coverage row has `resource`, `native` (query-string
parameters excluding paging), `exact` (boolean), `total` and `returned`
(integer counts), `complete` (total or limited), and a `release` object.
Term search has no partial-failure mode.

## Identifier normalization result

The object has `id`, the canonical CURIE string. No bare-accession match
causes `unknown_id`, and several matches cause `ambiguous_id`.

## Identifier resolution result

The object has canonical `id` and a nonempty `urls` array of resolver URL
strings. A scheme without a URL format causes `no_resolver`.

## Domains list

Domains list has a `domains` array. Each row has `id` and `summary` strings,
`includes` (domain ID array), effective `resources` (resource ID array),
`beliefs_contract` (name string or null) and `beliefs_contract_version`
(integer or null). Included domains contribute resources once.

## Resources list

Resources list has selected `domain` and `resources` rows. Each row has
`id`, `name`, `roles` (role array) and `prefixes` (owned canonical-prefix
array, including local declarations). Role/route filters narrow the rows.

## Resource show

Resource show emits `id`, `name`, `adapter` (Python module string),
`roles`, `prefixes`, `routes`, `local_prefixes`, `bare_normalize`, `fairsharing`
and `pitfalls`. Roles and routes are nonempty. Prefixes and pitfalls can be
empty. `bare_normalize` is a boolean indicating whether `id normalize` may infer
this resource's owned prefixes from bare accessions (default `true`; when
`false`, callers supply qualified CURIEs). `fairsharing` is a string or null.
Here prefixes contains the explicit registry-prefix array. Local declarations
are separate in local_prefixes.

Allowed roles are identifier-scheme, vocabulary, ontology, data-model,
repository, knowledgebase and literature-index. Resource IDs and domain IDs
are catalog names. Catalog loading rejects missing required keys, mistyped
fields and unknown keys. Validation rejects unknown roles, invalid prefix
ownership and incomplete pitfalls.

## Local prefix

The local-prefix map is keyed by prefix. Each value contains `pattern` (refs-dialect
pattern string), `uri_format` (string with a $1 substitution) and `why`
(explanatory string).

## Pitfall

A pitfall has nonempty string `summary`, `handling`
and `evidence`. Public evidence retains probe facts and usable references.

## Route

| Route field | Meaning and allowed values |
| --- | --- |
| `kind` | rest, query-endpoint, bulk or local-tool. |
| `url` | Route base/location string. |
| `min_interval` | Numeric minimum seconds between requests for this service. |
| `service` | Shared throttle identifier string. |
| `auth` | none, optional-key or required-key. |
| `reports_total` | Boolean describing provider total support. |
| `key_env` | Credential environment-variable name string, or null. |
| `keyed_min_interval` | Numeric seconds when a key is supplied, or null. |
| `key_param`, `key_header` | Query/header placement name strings, or null. |
| `key_scheme` | Optional credential header scheme (`bearer`), or null. When `bearer`, the route must be authenticated with `key_env`, `key_header` set to `Authorization`, and no `key_param`; the client sends `Authorization: Bearer <token>`. |
| `page_cap` | Integer page cap, or null. |

All route fields are emitted, including null optional fields. Authenticated
routes require a key variable and a query/header placement. A route with
`key_scheme = "bearer"` requires an authenticated `Authorization` header route
without `key_param`; omitting `key_scheme` preserves raw key placement. A
missing required credential is refused before transport. Names are exposed,
credential values are redacted from request traces and errors.

## Skills build result

The object has `written` and `unchanged`, both arrays of paths relative to
the checkout root. It reports catalog-generated skill regions. Missing skills,
invalid markers or an invalid catalog cause failure.

## Findings and errors

A validation failure from check or id check emits
`{"findings": [{"kind": "...", "where": "...", "detail": "..."}]}` on stdout
with exit 1. Each field is a string identifying the violation, input/path
and explanation. Success is silent.

A runtime failure emits `{"error": {"kind": "...", "detail": "..."}}` on
stderr with exit 1. Wrong command usage emits text on stderr with exit 2.
Pretty mode renders human text for the same outcomes. Check diagnosis is
documented in the [CLI guide](cli.md#diagnose-output-and-failures) and
[developer guide](../AGENTS.md#diagnosing-checks).
