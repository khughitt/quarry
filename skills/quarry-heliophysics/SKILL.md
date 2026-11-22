---
name: quarry-heliophysics
description: Use with the quarry skill when searching solar and solar-wind resources: image and in-situ archives, index series, event knowledgebases, SPASE vocabularies, or heliophysics literature. Says which resource answers which question, how to filter by observation time, and what to check before acquisition.
---

# quarry: heliophysics

Use `--domain heliophysics` (or set `domain = "heliophysics"` in `quarry.toml`). This domain
includes `core` (`zenodo`, `figshare`, `pubmed`, `europepmc`).

The effective default search set has five resources: `vso`, `cdaweb`, `hek`, plus `zenodo` and
`figshare` from `core`. Time-filtered searches (`--during`) run `vso`, `cdaweb` and `hek` while
skipping `zenodo` and `figshare` with reasons; ordinary phrase searches run `zenodo` and
`figshare` while skipping resources without free-text search. Name any other resource with
`--in`.

## Which resource answers what

- **Images of the Sun at a time**: `vso` (index across archives including JSOC, SDAC and NSO;
  in the default set), `jsoc` (SDO HMI and AIA), `soar` (Solar Orbiter), `soho` and
  `stereo-ssc` (bulk directory trees), and `helioviewer` (browse JP2 images with embedded FITS
  headers, nearest instant only).
- **Solar wind and index series**: `cdaweb` (in-situ and OMNI datasets; in the default set; use
  HAPI for reproducible cuts), `omniweb` (SPDF OMNI bulk tree; CGI form is described only, not
  used for acquisition), `goes-xrs` (NOAA NCEI GOES X-ray L2 granules and legacy flare lists;
  SWPC rolling JSON is not an archive), and `silso` (WDC-SILSO sunspot number, one file per
  series).
- **Events and active regions**: `hek` (detections and reports, by overlap; in the default
  set), `donki` (CCMC curated space-weather events and notifications, by start time),
  `swpc-events` (NOAA SWPC forecaster event lists and Solar Region Summaries via NCEI's HTTPS
  daily mirror; owns `noaa-ar:` for identifier validation and SRS resolution only), and
  `cdaw-cme` (SOHO LASCO CME catalog v2, by month).
- **Datasets, vocabularies and labels**: `hpde` (SPASE metadata descriptions; never read
  coverage dates from it), `spase` (`spase.measurement:` and `spase.region:` from versioned
  XSD 2.7.2 via `term search|show`), `goes-class` (`goes-class:` inline flare-class scale via
  `term search|show`), and `noaa-ar:` active-region CURIEs (`show` and `files` on `noaa-ar:`
  are refused before transport because NOAA provides no stable per-region lookup; daily files
  use `swpc-events:` CURIEs).
- **Literature**: `arxiv` (applies `cat:astro-ph.SR` by default in every lens; `--native` sends
  the caller's query unchanged; sets `bare_normalize = false`, so pass qualified `arxiv:<id>`
  CURIEs) and `ads` (`bibcode:`, requires `ADS_DEV_KEY` Bearer token, month-aligned
  `--since`/`--until`). Neither sits in the default search set; select them with `--in`.

## Observation time and filters

- `--during START/END` bounds observation time on half-open UTC `[S, E)` (date `END` includes
  its whole day; datetime `END` is exclusive; uppercase `T` and `Z` only). Default search covers
  `vso`, `cdaweb` and `hek` (`time: overlap`, `match: none` when phraseless). `donki` selects by
  event start time at 1-minute precision (`time: start`) and emits a warning that events begun
  before `START` are omitted. `--since` and `--until` retain their release-date meaning
  (`jsoc`, `soar`, `ads`, `arxiv`).
- `--at INSTANT` requires naming Helioviewer explicitly with `--in` and an exact source
  nickname, for example:
  `quarry search --domain heliophysics --in helioviewer --at 2025-01-15T00:00:00Z --instrument "AIA 335"`.
  Default-set `--at` fails with `unsupported_filter` until VSO's `near` is shown to return the
  nearest record without `start` and `end`. Missing, generic (such as `"AIA"`), unmatched or
  ambiguous `--instrument` selectors are refused before requesting the closest image; add
  `--observatory` when a nickname spans multiple observatories.
- `--instrument` and `--observatory` narrow archives and `hek` where declared.
- `--measurement` accepts SPASE `MeasurementType` terms only when an adapter can enforce an
  exact mapping. No adapter currently has verified exact mappings; approximate-only mappings are
  refused before transport with `--native` syntax hints (`<physobs>...</physobs>` on `vso`,
  `instrumentType=...` on `cdaweb`).

## Partial-provider risks, terms and acquisition

- **VSO provider reports and optional SunPy routes**: `vso` federates multiple providers and
  retains per-provider counts and errors; any provider error, omitted count or truncated
  provider reply fails with `incomplete_listing` (unless `-n` is satisfied from trimmed rows)
  and records provider warnings. Catalogued `local-tool` routes (`sunpy.net.*`) document
  optional local SunPy entry points and are never invoked by `quarry` commands; `just live`
  reports missing SunPy modules or missing keys (`ADS_DEV_KEY`, `JSOC_NOTIFY_EMAIL`) as failed
  routes.
- **Cuts versus files**: a time cut is a request, not a stored file, on `cdaweb` (HAPI `/data`
  gives the reproducible GET URL; `files cdaweb:<dataset>` returns an empty list because a
  dataset CURIE carries no interval or variables, and REST data jobs' temporary URLs are never
  cited), a per-item science download on `soar`, and a record-set specifier on `jsoc` (online
  SUMS paths are resolved at request time via `rs_list R=1` in preference to asynchronous
  `jsoc_fetch` export).
- **Checksums, staleness and catalog caveats**: published SHA-256 digests exist only at `silso`
  (`manifest-paired.sha256`); other archives omit `expected` checksums, so date and hash every
  downloaded file. `hpde` coverage dates lag repositories by years; `donki`'s API base moved
  once and non-JSON responses fail as `upstream_failed`; `swpc-events` uses NCEI's HTTPS daily
  mirror (SWPC's archive is FTP-only yearly tarballs, and the latest three days regenerate
  nightly); `cdaw-cme` is incomplete by construction with a separate data-gap list and rows are
  revised long after the event; `omniweb` and `soho` are revised in place.
- **Provider terms**: most heliophysics services do not serve machine-readable terms at their
  API routes (and CDAWeb's implied terms path returned 404). Check each provider's terms of use
  and citation/acknowledgement requirements before the first acquisition.

## Resource notes

<!-- quarry:resources start — generated by quarry skills build; edit the catalog -->
### Joint Science Operations Center (SDO HMI and AIA) (`jsoc`)

Roles: repository. Prefixes: `jsoc`.

Routes:

- rest `http://jsoc.stanford.edu/cgi-bin/ajax`: every 1 s; reports a total
- rest `http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_fetch`: every 1 s; key JSOC_NOTIFY_EMAIL (required-key); no total
- bulk `http://jsoc.stanford.edu`: every 1 s; no total

Pitfalls:

- record-set times are TAI unless suffixed _UTC, and prime-key slot ranges include their end and snap to nearby slot centers. Handling: every time the adapter emits carries _UTC; TAI T_REC values returned by HMI series are converted to UTC and results are trimmed to half-open [START, END).
- a time keyword in a where clause must be wrapped in $(); a bare string is a Postgres type error. Handling: release-date comparisons compile to [? DATE >= $(YYYY.MM.DD_00:00:00_UTC) ?] and [? DATE < $(YYYY.MM.DD_00:00:00_UTC) ?].
- segment URLs are SUMS paths valid only while the record is online, and they redirect to jsoc1. Handling: URLs are resolved at request time with rs_list seg=**ALL** R=1; offline records and non-SUMS placeholders never invent URLs; the reproducible part of an acquisition is the record-set specifier.
- the export route refuses without a registered email, is asynchronous (exp_request then exp_status until status 0), and can complete with an empty file list; no digests on any route. Handling: required-key route (JSOC_NOTIFY_EMAIL); acquisition prefers rs_list with R=1 (online SUMS paths) over export; empty export completion fails upstream_failed rather than inventing locations.

### Virtual Solar Observatory (`vso`)

Roles: repository. Prefixes: `vso.file`.

Routes:

- query-endpoint `http://vso.nso.edu/cgi-bin/VSO/PROD/vsoi_wsdl.cgi`: every 1 s; reports a total
- local-tool `sunpy.net.vso`: every 0 s; reports a total

Pitfalls:

- SOAPAction must be 'http://virtualsolar.org/VSO/VSOi#Query' or '#GetData'; a bare method name is a soap:Fault. Handling: set by the adapter on every SOAP request.
- fileids are minted by providers (JSOC, SDAC) and may contain colons or slashes; VSO owns the canonical vso.file locator encoding provider and opaque fileid. Handling: records encode vso.file:<provider>/~<unpadded Base64url of UTF-8 fileid>; show and files validate a round trip and send provider and fileid unchanged to link-only GetData.
- the API documentation wiki pages are gone; the WSDL is the only description. Handling: the adapter is written against the WSDL.
- VSO time/near requires time/start and time/end and cannot answer an unbounded --at instant query. Handling: VSO declares --during (overlap) only and refuses --at before transport.

### Coordinated Data Analysis Web (SPDF) (`cdaweb`)

Roles: repository. Prefixes: `cdaweb`.

Routes:

- rest `https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys`: every 1 s; no total
- rest `https://cdaweb.gsfc.nasa.gov/hapi`: every 1 s; no total
- bulk `https://cdaweb.gsfc.nasa.gov/sp_phys/data`: every 1 s; no total
- local-tool `sunpy.net.cdaweb`: every 0 s; no total

Pitfalls:

- a time cut becomes a file only through a service-side job, at a temporary URL that differs per request. Handling: bare cdaweb:<dataset> ids carry no interval or variables, so files returns an empty inventory without issuing orig_data, data or HAPI cut requests.
- orig_data returns whole period granules overlapping the interval (a month for a day). Handling: every file carries its own coverage beside the asked interval.
- HAPI /data is one stable GET URL per cut (id, parameters, time.min, time.max, format), streamed with a header; the only CDAWeb route whose cut URL is reproducible. Handling: a cut is emitted as a HAPI URL, never as the REST job's temporary URL.
- no checksums on any route; LastModified and ETag only. Handling: expected omitted; size and LastModified recorded.
- the terms-of-use path the documentation implies is 404. Handling: terms are read: open; the resource is used under SPDF's general rules.

### Solar Orbiter Archive (`soar`)

Roles: repository. Prefixes: `soar`.

Routes:

- query-endpoint `https://soar.esac.esa.int/soar-sl-tap/tap`: every 1 s; reports a total
- rest `https://soar.esac.esa.int/soar-sl-tap/data`: every 1 s; no total
- local-tool `sunpy.net.soar`: every 0 s; reports a total

Pitfalls:

- TAP_SCHEMA names carry the schema prefix (soar.v_sc_data_item) while FROM takes the bare view name. Handling: prefix only in TAP_SCHEMA lookups; queries select from bare v_sc_data_item ordered by begin_time, data_item_id.
- no checksums in the views or on the download; filesize and Content-Length agree. Handling: expected omitted; Content-Length checked against filesize.
- TAP_SCHEMA.columns has no units or descriptions for the data-item view. Handling: column meaning from documentation, not the service.

### SOHO archive (SDAC bulk tree) (`soho`)

Roles: repository. Prefixes: `soho`.

Routes:

- bulk `https://umbra.nascom.nasa.gov/pub`: every 1 s; no total

Pitfalls:

- files carry no content-type and no digest. Handling: type by extension; expected omitted.
- the archive is curated after the fact (deletions logged); three doors with different coverage. Handling: every download is dated and hashed; search goes through vso.
- LASCO level-0.5 is organised by YYMMDD and the sample day's directory was not found. Handling: read: open; EIT (lz/YYYY/MM) is the proven tree and unverified archive doors are withheld.

### STEREO Science Center data tree (`stereo-ssc`)

Roles: repository. Prefixes: `stereo-ssc`.

Routes:

- bulk `https://stereo-ssc.nascom.nasa.gov/data/ins_data`: every 1 s; no total

Pitfalls:

- the all-days parent listing times out at 120 s. Handling: list by day; never the parent.
- file names are observation times to the second; a guessed name is 404. Handling: list the day, then match.
- files carry no checksums. Handling: expected omitted; size and Last-Modified recorded from HEAD.

### Helioviewer API (`helioviewer`)

Roles: repository. Prefixes: `helioviewer`.

Routes:

- rest `https://api.helioviewer.org/v2`: every 1 s; no total

Pitfalls:

- getClosestImage answers with the nearest image, which may lie outside an asked interval. Handling: the returned date is recorded in observation time; --at compiles here, --during is refused before transport.
- an unknown image id is HTTP 500 with a JSON error, not 404. Handling: errno 24 maps to not_found without retrying.
- JPIP streaming URIs are not HTTP(S) file downloads and browse images carry no checksums. Handling: JPIP URIs stay in domain metadata separate from HTTP(S) acquisition URLs; metadata lookup uses getJP2Header without downloading image bytes.

### GOES X-ray sensor data and flare lists (NOAA NCEI, SWPC) (`goes-xrs`)

Roles: repository. Prefixes: `goes`.

Routes:

- bulk `https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes`: every 1 s; no total
- rest `https://services.swpc.noaa.gov/json/goes/primary`: every 1 s; no total
- local-tool `sunpy.net.dataretriever.sources.goes`: every 0 s; no total

Pitfalls:

- file names carry a processing version (v2-2-1, v2-2-2) that changes; a constructed name is 404. Handling: list the month directory and match the day.
- the SWPC JSON products are rolling windows, overwritten; not an archive. Handling: no acquisition from them; NCEI granules are the record.
- the legacy flare event lists end in 2017; later flares are in L2 flsum granules. Handling: two series under one id, split by epoch.

### WDC-SILSO sunspot number (`silso`)

Roles: repository. Prefixes: `silso`.

Routes:

- bulk `https://www.sidc.be/SILSO/DATA`: every 1 s; no total

Pitfalls:

- one file per series, reissued monthly with the recent rows provisional. Handling: a dataset is a dated release: the file, its manifest sha256 and Last-Modified.
- missing values are -1; provisional rows carry '*'. Handling: documented; never filled.
- digests are published in manifest-paired.sha256 and verify (the one surveyed resource with digests). Handling: expected is taken from the manifest.

### OMNIWeb and the SPDF OMNI bulk tree (`omniweb`)

Roles: repository. Prefixes: `omni`.

Routes:

- bulk `https://spdf.gsfc.nasa.gov/pub/data/omni`: every 1 s; no total
- rest `https://omniweb.gsfc.nasa.gov/cgi/nx1.cgi`: every 1 s; no total

Pitfalls:

- the CGI returns data inside an HTML page, not as a file. Handling: described, not used for acquisition; the bulk tree and cdaweb are.
- the data set is revised in place as better source data arrive. Handling: every download dated and hashed.

### Heliophysics Events Knowledgebase (`hek`)

Roles: knowledgebase. Prefixes: `hek`.

Routes:

- rest `https://www.lmsal.com/hek/her`: every 1 s; no total
- local-tool `sunpy.net.hek`: every 0 s; no total

Pitfalls:

- interval search is by overlap and returns events that began before the interval or touch an endpoint. Handling: event_starttime and event_endtime are returned with every record and trimmed to half-open [START, END) overlap.
- no total count; overmax flags that more exist. Handling: page until overmax is false or the cap is hit; coverage says limited when capped and fails incomplete_listing if overmax stays true.
- the spatial box is mandatory in the column search. Handling: the adapter supplies the full-disk helioprojective box by default.
- records carry scientific values (peak flux, GOES class, intensities). Handling: the adapter requests structural metadata columns only via return= and exposes no scientific values in records.

### CCMC DONKI (`donki`)

Roles: knowledgebase. Prefixes: `donki`.

Routes:

- rest `https://ccmc.gsfc.nasa.gov/DONKI-API/get`: every 1 s; no total

Pitfalls:

- the API base moved on 2026-09-30; the old base answers HTTP 200 with an HTML page. Handling: content-type is checked on every response; non-JSON is upstream_failed.
- records are revised (versionId) and link to each other (linkedEvents). Handling: versionId and submissionTime are kept with every record and linkedEvents become donki: xrefs.
- date-wide queries select by event start time and record timestamps have minute precision (YYYY-MM-DDTHH:MMZ). Handling: --during declares start, widens to whole calendar days, trims by verified provider timestamps, and refuses sub-minute query bounds before transport.
- no native per-id lookup endpoint exists. Handling: show donki:<id> parses UTC date and event kind, queries that kind's single-day window, and requires an exact match to the response ID field.

### NOAA SWPC edited event lists and Solar Region Summaries (`swpc-events`)

Roles: knowledgebase. Prefixes: `noaa-ar`, `swpc-events`.

Routes:

- bulk `https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports`: every 1 s; no total
- rest `https://services.swpc.noaa.gov/text`: every 1 s; no total
- local-tool `sunpy.net.dataretriever.sources.noaa`: every 0 s; no total

Pitfalls:

- the SWPC archive (ftp://ftp.swpc.noaa.gov/pub) is FTP only (https resets) and past days live inside yearly tarballs; the kernel's acquisition speaks HTTP(S). Handling: acquisition uses NCEI's HTTPS mirror, one file per day (solar_region_summaries/YYYY/MM/YYYYMMDDSRS.txt, solar_event_reports/YYYY/MM/YYYYMMDDevents.txt); the FTP warehouse is documented here and not shipped as an executable route.
- the last three days' lists are regenerated nightly and current SWPC text products overwrite in place. Handling: downloads under 3 days old are provisional; current SWPC text is not an archive.
- NOAA active region numbers (noaa-ar:) have no stable per-region lookup endpoint. Handling: show and files on noaa-ar: refuse with unsupported_id before I/O and direct callers to the Solar Region Summary resolver (https://www.swpc.noaa.gov/products/solar-region-summary).

### SOHO LASCO CME catalog (CDAW, version 2) (`cdaw-cme`)

Roles: knowledgebase. Prefixes: `cdaw-cme`.

Routes:

- bulk `https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver`: every 1 s; no total

Pitfalls:

- a CME is identified by (date, first-appearance time, central PA) and rows are edited a year or more after the event. Handling: monthly files (univYYYY_MM.txt) are the discovery units and carry the file's Last-Modified.
- the catalog is incomplete by construction (manual identification) and has a separate LASCO data-gap list. Handling: consult CDAW's data-gap table before interpreting missing periods; this catalog covers version 2 only.

### HPDE SPASE metadata registry (`hpde`)

Roles: knowledgebase. Prefixes: `spase`.

Routes:

- rest `https://spase-metadata.org`: every 1 s; no total
- bulk `https://raw.githubusercontent.com/hpde`: every 1 s; no total

Pitfalls:

- coverage dates in registry records lag the repository by years (OMNI StopDate 2020-01-01; CDAWeb 2026-07-08). Handling: coverage is never read from the registry; HPDE declares no time relation and omits TemporalDescription from records.
- hpde.io may not resolve; the same records are served by spase-metadata.org and GitHub. Handling: two routes (spase-metadata.org JSON and raw.githubusercontent.com/hpde XML) to one record set; hpde.io is never contacted.
- records exist in several schema minor versions at once (2.7.0, 2.7.1, 2.7.2). Handling: the adapter reads Version and tolerates SPASE 2.x minor versions.

### SPASE data model and vocabularies (`spase`)

Roles: data-model, vocabulary. Prefixes: `spase.measurement`, `spase.region`.

Routes:

- bulk `https://spase-group.org/data/schema`: every 1 s; no total

Pitfalls:

- vocabularies (MeasurementType, ObservedRegion) are XSD enumerations; there is no term service. Handling: terms are read from the versioned XSD (spase-2.7.2.xsd).
- documentation URLs move between versions; guessed paths 404. Handling: only /data/model/ and the versioned XSD are cited.

### GOES soft X-ray flare class (`goes-class`)

Roles: vocabulary. Prefixes: `goes-class`.

Routes:

- bulk `https://www.swpc.noaa.gov/phenomena/solar-flares-radio-blackouts`: every 1 s; no total

Pitfalls:

- a class is a letter (A B C M X, decades of 0.1–0.8 nm peak flux from 1e-8 W/m2) plus an optional decimal multiplier, and the measuring satellite differs between lists. Handling: pattern ^[ABCMX]([0-9]+(\.[0-9]+)?)?$; the satellite stays with the record that carries the class.

### NASA Astrophysics Data System (`ads`)

Roles: literature-index. Prefixes: `bibcode`.

Routes:

- rest `https://api.adsabs.harvard.edu/v1`: every 1 s; key ADS_DEV_KEY (required-key); reports a total

Pitfalls:

- every API call needs a Bearer token; unauthenticated requests return HTTP 401 JSON. Handling: required-key route (ADS_DEV_KEY with Authorization: Bearer); missing key refuses before transport without leaking credentials.
- ADS pubdate carries year (YYYY-00-00), month (YYYY-MM-00) or day (YYYY-MM-DD) precision rather than always exact days. Handling: records emit pubdate at its known precision without fabricating day or month; --since/--until require month-aligned bounds before transport and fail if a returned record has coarser precision whose membership in the requested interval cannot be established; use --native for exact pubdate:[...] queries.
- API documentation pages are a client-side application (curl gets HTTP 202 with an empty body) and ADS is metadata-only in quarry. Handling: rate limits (x-ratelimit-limit: 5000 per token per day observed in headers) and terms are read in a browser; files returns an empty inventory and abstracts are omitted.

### arXiv (`arxiv`)

Roles: literature-index. Prefixes: `arxiv`.

Routes:

- rest `http://export.arxiv.org/api/query`: every 3 s; reports a total

Pitfalls:

- ordinary --in arxiv searches apply cat:astro-ph.SR across every lens (core, biology, heliophysics). Handling: the selector is exposed in native coverage and reported in a coverage warning; use --native (for example, 'cat:cs.SE AND all:"..."') to search another category without the default solar filter or warning.
- arXiv's upstream Bioregistry pattern also matches bare accessions owned by other resources. Handling: bare_normalize = false opts arxiv out of bare ID inference; write qualified arxiv:<id> CURIEs for id normalize, show and files.
- submittedDate range brackets must be URL-encoded and the API manual requests a 3-second delay between calls. Handling: the adapter URL-encodes query parameters, compiles --since/--until to submittedDate:[YYYYMMDD0000 TO YYYYMMDD2359], and throttles at min_interval = 3.0 s.
- the Atom feed's top-level <title> is the query echo, not a record. Handling: the adapter parses <entry> elements separately from feed-level metadata and fails on malformed XML or count changes.
- descriptive metadata is CC0 1.0 while e-prints carry their own licences. Handling: metadata-only scope (files returns an empty inventory with no PDF acquisition, abstracts omitted); per-record licence metadata is preserved when present.
<!-- quarry:resources end -->
