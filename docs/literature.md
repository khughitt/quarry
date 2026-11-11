# PubMed and Europe PMC

Type: guide, data model; document types 1. Publication metadata and availability
fields are currently unversioned. This covers literature queries and eligible
full-text file locators. The [CLI guide](cli.md) covers shared commands, and
the [JSON model](output.md) defines record, coverage and file envelopes.

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
