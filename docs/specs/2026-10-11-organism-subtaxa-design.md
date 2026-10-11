# `--organism` includes subtaxa, in GEO and ENA alike

Status: draft for review (task quarry-695d0a). Amends spec §8.2 of the quarry domain-layers
design (science `docs/specs/2026-10-10-quarry-domain-layers-design.md`).

## 1. Problem

`--organism` takes a taxon name or an `ncbitaxon:` CURIE, and §8.2 never says whether it
reaches the taxon's descendants. The two adapters that take taxon ids answer differently,
and both report `"match": "exact"`:

| `--organism`        | GEO compiles              | ENA compiles                  |
|---------------------|---------------------------|-------------------------------|
| `ncbitaxon:10088`   | `txid10088[ORGN]`: subtree | `tax_eq(10088)`: that node only |
| `Mus`               | `"Mus"[ORGN]`: subtree     | `scientific_name="Mus"`: that label only |

So `--organism ncbitaxon:10088` (the genus Mus) merges every Mus species from GEO with
almost nothing from ENA.

Live counts, 2026-10-11 (GEO series under `gse[ETYP]`, ENA studies):

| query                         | GEO     | ENA    |
|-------------------------------|---------|--------|
| Mus, node only                | 237     | 226    |
| Mus, subtree                  | 101,987 | 94,364 |
| Mus musculus, node only       | 101,788 | 93,411 |
| Mus musculus, subtree         | 101,819 | 93,795 |

Names add problems of their own. GEO maps a name through Entrez: it accepts common names
(`"human"[ORGN]` = `txid9606[ORGN]`) and silently unions homonyms (`"Bacillus"[ORGN]` is
495 bacterial series plus 1 from the stick-insect genus). `[ORGN:noexp]` returns 0 for
any name, so GEO can be restricted to a single node only by taxon id. ENA has no way to
search a subtree by name: `tax_tree` takes an id only.

## 2. Decision

**`--organism X` means the taxon X and every taxon below it.**

This is what a biologist asking for an organism means. It is NCBI's default and the
default in ENA's browser. It makes a genus or clade query (`Mus`, `Primates`) useful,
and it keeps records labelled with a subspecies or strain-level taxon (384 ENA studies
under Mus musculus) inside a species query.

Rejected: node only (`tax_eq`, `[ORGN:noexp]`). It is more literal, but a genus query
returns almost nothing, a species query silently loses records filed under subspecies,
and GEO would still need a name lookup, because `[ORGN:noexp]` with a name finds nothing.
Both meanings cost one name lookup, in ENA or in GEO, so the choice rests on what the
filter is for.

## 3. Per resource

- **GEO.** Unchanged: `txid<N>[ORGN]` for a CURIE and `"<name>"[ORGN]` for a name. Both
  already include subtaxa. Tests pin both forms as the subtree meaning.
- **ENA.** A CURIE compiles to `tax_tree(<N>)`. A name is resolved to one taxon id first,
  through ENA's taxonomy service (`https://www.ebi.ac.uk/ena/taxonomy/rest/any-name/<name>`,
  a second route on the `ena` resource that shares the `ebi-ena` throttle), and then
  compiles to `tax_tree(<id>)`:
  - one taxon: use it;
  - none: `not_found`, "ENA's taxonomy has no taxon named X";
  - more than one: `ambiguous_id`, naming each candidate as `ncbitaxon:<id> (<scientific
    name>, <rank>)` and asking for the CURIE (`mouse` is Mus musculus and Mus; `Bacillus`
    is a bacterial genus and an insect genus).

  The lookup is a request, so it runs in the adapter's execute step, after every static
  refusal (§8.2's ordering holds). Under `--allow-partial`, a lookup failure is ENA's
  `failed` coverage row, like any other request failure. The coverage row's `native` is
  the compiled query, so it shows the `tax_tree(<id>)` that ran. `prepare` keeps refusing
  a name containing `"` or `*`, as ENA phrases already do.
- **BioStudies.** Its `facet.organism` matches one exact label and cannot include
  subtaxa. It is not skipped: it stays in biology's default set, since dropping it would
  lose ArrayExpress from every organism search. Instead, its coverage row carries a
  warning, which the report repeats: "biostudies matches the organism label X only, not
  its subtaxa". Normalising the label (case, common names) remains quarry-f992fd.

GEO and ENA can still part on a homonym: GEO unions the taxa and ENA refuses with
`ambiguous_id`. That disagreement is loud: without `--allow-partial` the search fails
and names the CURIEs to pass. It is not a silent difference in results.

## 4. Spec text (§8.2, Domain filters)

Replace the Domain filters bullet with:

> - **Domain filters** are declared by a domain (biology: `--organism`, which takes a
>   taxon name or `ncbitaxon:` CURIE and matches that taxon and every taxon below it).
>   A resource that resolves names to taxa refuses a name naming no taxon (`not_found`)
>   or more than one (`ambiguous_id`, listing the CURIEs). BioStudies matches its
>   organism label only, and its coverage row warns that subtaxa are not included.

The catalog gets an ENA pitfall: "tax_eq matches one taxon node and tax_tree takes ids
only"; handling: "a CURIE compiles to tax_tree; a name is resolved through the taxonomy
service first". BioStudies gets a pitfall for the label-only facet.

## 5. Testing

No test reaches the network (AGENTS.md):

- GEO: `compile_query` pins `txid10088[ORGN]` and `"Mus"[ORGN]`.
- ENA: `compile_query` pins `tax_tree(10088)` for a CURIE. Replayed exchanges cover the
  name paths: one hit compiles `tax_tree` (asserted on the search request and on the
  coverage `native`); an empty list is `not_found`; two hits are `ambiguous_id` naming
  both CURIEs; a malformed body is `upstream_failed`. A `--allow-partial` run turns a
  lookup failure into ENA's failed row.
- BioStudies: with `--organism`, the coverage row and the report carry the subtaxa warning.
- A recorded scenario (`just record ena`) captures one real `any-name` lookup, so the
  response shape is pinned against the live service.

## 6. Out of scope

- Resolving names centrally, once for all resources. That would make GEO and ENA use
  the same taxon even for homonyms and common names, but it needs a network step before
  adapters compile, which breaks §8.2's "every static refusal before the first request".
  It can come back with the NCBITaxon ontology adapter (quarry-06c10c).
- BioStudies label normalisation (quarry-f992fd).
