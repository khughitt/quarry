# quarry: the heliophysics domain

Status: draft 2026-10-11, for review. Written from the sun program's phase-1 resource
survey (sun repository, `docs/reports/2026-10-10-phase-1-survey/`, task `sun-374ea3`),
against the approved quarry spec (`2026-10-10-quarry-domain-layers-design.md`) and the
catalog as implemented in `src/quarry/catalog.py` on 2026-10-11. Source goal
`sci-ba7150`. The extensions this domain asks for are filed as `quarry-b794e6`,
`quarry-bc633c`, `quarry-fadd26`, `quarry-bacd40` and `quarry-e2c1f1`; this spec records
how the domain is written while they are undecided.

Sources: the survey's dossiers (`dossiers/<id>.md`, one per resource, each ending in the
draft TOML copied here) and probe logs (`probes/<id>/log.md`, cited as `probes/<id>/<n>`
in every `evidence` field); the quarry spec §4 (model), §6 (catalog format), §7
(identifiers), §8.2 (filters), §9 (acquisition), §14 (other domains).

## 1. What this is

The first quarry domain outside biology and the first test of the spec's §14: the Sun
and the solar wind's data resources as a lens over one CLI. Twenty-one resources,
twenty with a resource file; four domain filters; eight pitfall families observed live.
Magnetospheric and ionospheric resources (Kp, Dst) are out of scope through the sun
program's phase 3 and are not listed.

What it is not: adapters. Every resource file names an adapter module that does not
exist yet; `quarry check` reports `missing_adapter` for each until the implementation
task builds it, and this spec says what each adapter compiles (§5). Nor does it change
the verbs: the extensions in §7 are a request field, a domain filter, a route kind, a
role and a check, each allowed by §14.

## 2. Decisions

| Decision | Chosen | Rejected |
|---|---|---|
| Domain name | `heliophysics` (the science's name, as `biology`) | `solar`, `sun` (the program's repository name, not a science) |
| Includes | `core` (Zenodo, figshare, PubMed) | a `physics` layer: nothing surveyed needs it, and quarry's §14 physics row (HEPData, INSPIRE) shares no resource with this domain |
| Literature | `arxiv` in `core` with the domain's default category `astro-ph.SR`; `ads` listed in this domain | `ads` in `core`: it is the astrophysics index, keyed, and no other domain asked for it |
| Beliefs contract | none declared | `beliefs_contract = "heliophysics"`: no such contract exists, and the sun framing decides the contract by a worked example in its phase 3, not here |
| Time | a domain filter `coverage` (observation interval), with `--at` for an instant | a shared coverage filter: one time-indexed domain is not evidence for the semantics a second will want (quarry-bc633c) |
| Identifiers | `local_prefixes` for every scheme but `arxiv` and `doi` | waiting for Bioregistry: its search for "solar" and "helio" is empty (probes/goes-class/03, 04) |
| OMNI ownership | `cdaweb` owns `cdaweb:` dataset ids and serves OMNI; `omniweb` owns the SPDF file tree under `omni:` | one OMNI resource: it has three doors with different retrieval units |
| Registry and index | `hpde` and `vso` carry `knowledgebase` and `repository` with the misfit in a pitfall | inventing a role here: that is quarry-bacd40's decision |
| HAPI | `query-endpoint` with a pitfall naming the grammar | a `hapi` kind: quarry-fadd26's decision |

## 3. The domain file

```toml
id = "heliophysics"
summary = "The Sun and the solar wind: image and in-situ archives, index series, event knowledgebases, SPASE"
includes = ["core"]
resources = [
  "jsoc", "vso", "cdaweb", "soar", "soho", "stereo-ssc", "helioviewer",
  "goes-xrs", "silso", "omniweb",
  "hek", "donki", "swpc-events", "cdaw-cme",
  "hpde", "spase", "goes-class",
  "ads", "arxiv",
]
search_default = ["vso", "cdaweb", "hek"]   # one index over archives, one in-situ repository, one event knowledgebase
filters = ["coverage", "instrument", "observatory", "measurement"]
```

`arxiv` is listed here as well as in `core` so that the domain can carry its default
category; the catalog's `effective_resources` lists a resource once. No
`beliefs_contract`, no `facet_namespaces` (§2).

`search_default` is three, not nine: `vso` already federates JSOC, SDAC and NSO; `soar`
is a per-mission archive; the bulk trees have no search; the knowledgebases other than
HEK answer narrower questions. A user names the rest with `--in`.

## 4. The resource files

One per eligible resource, in the catalog's live shape (`RESOURCE_KEYS`, `ROUTE_KEYS`,
`local_prefixes` as tables with `pattern`, `uri_format`, `why`; `auth` in `none`,
`optional-key`, `required-key`; a `service` per throttle). `min_interval` is the survey's
politeness interval where the service documents none, and the documented one where it
does (arXiv). Every `evidence` cites a survey probe. `fairsharing` is omitted
throughout: the survey could not read FAIRsharing without an account.

### 4.1 Image and in-situ archives

```toml
# resources/jsoc.toml
id = "jsoc"
name = "Joint Science Operations Center (SDO HMI and AIA)"
roles = ["repository"]
adapter = "quarry.adapters.jsoc"

[local_prefixes.jsoc]
pattern = "^[a-z_]+\\.[A-Za-z0-9_]+(\\[[^\\]]*\\])*$"
uri_format = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_info?op=rs_list&ds=$1&key=T_REC"
why = "JSOC series names and record-set specifiers; Bioregistry has no prefix (probes/jsoc/10)"

[[routes]]
kind = "rest"
url = "http://jsoc.stanford.edu/cgi-bin/ajax"
min_interval = 1.0
service = "jsoc"
auth = "none"
reports_total = true                   # rs_summary count (probes/jsoc/02)

[[routes]]
kind = "rest"                          # the export service: builds a file list on request
url = "http://jsoc.stanford.edu/cgi-bin/ajax/jsoc_fetch"
min_interval = 1.0
service = "jsoc"
auth = "required-key"                  # a validated email address, not a token
key_env = "JSOC_NOTIFY_EMAIL"
key_param = "notify"
reports_total = false

[[routes]]
kind = "bulk"
url = "http://jsoc.stanford.edu"
min_interval = 1.0
service = "jsoc"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "record-set times are TAI unless suffixed _UTC; a cut given in TAI starts 37 s early in UTC"
handling = "every time the adapter emits carries _UTC"
evidence = "sun phase-1 survey probes/jsoc/03"

[[pitfalls]]
summary = "a time keyword in a where clause must be wrapped in $(); a bare string is a Postgres type error"
handling = "time comparisons compile to [? KEY > $(…_UTC) ?]"
evidence = "probes/jsoc/14, 16"

[[pitfalls]]
summary = "segment URLs are SUMS paths valid only while the record is online, and they redirect to jsoc1"
handling = "URLs are resolved at request time with rs_list R=1; the reproducible part of an acquisition is the record-set specifier"
evidence = "probes/jsoc/04, 11, 19"

[[pitfalls]]
summary = "the export route refuses without a registered email; no digests on any route"
handling = "required-key route; expected omitted from acquisition requests; sizes by HEAD"
evidence = "probes/jsoc/05, 18, 11"
```

```toml
# resources/vso.toml
id = "vso"
name = "Virtual Solar Observatory"
roles = ["repository"]                 # misfit: an index over repositories; see quarry-bacd40
adapter = "quarry.adapters.vso"

[[routes]]
kind = "query-endpoint"                # SOAP rpc/literal; WSDL at https://sdac.virtualsolar.org/API/VSOi_rpc_literal.wsdl
url = "http://vso.nso.edu/cgi-bin/VSO/PROD/vsoi_wsdl.cgi"
min_interval = 1.0
service = "vso"
auth = "none"
reports_total = true                   # no_of_records_found per provider

[[routes]]
kind = "local-tool"
url = "sunpy.net.vso"
min_interval = 0.0
service = "vso"
auth = "none"
reports_total = true

[[pitfalls]]
summary = "SOAPAction must be 'http://virtualsolar.org/VSO/VSOi#Query'; a bare method name is a soap:Fault"
handling = "set by the adapter"
evidence = "sun phase-1 survey probes/vso/07, 10"

[[pitfalls]]
summary = "fileids are minted by providers (JSOC, SDAC), not by VSO; VSO owns no prefix"
handling = "records carry provider and fileid; GetData resolves URLs; ids are not CURIEs of vso"
evidence = "probes/vso/11, 12"

[[pitfalls]]
summary = "the API documentation wiki pages are gone; the WSDL is the only description"
handling = "the adapter is written against the WSDL"
evidence = "probes/vso/01, 03, 08"
```

```toml
# resources/cdaweb.toml
id = "cdaweb"
name = "Coordinated Data Analysis Web (SPDF)"
roles = ["repository"]
adapter = "quarry.adapters.cdaweb"

[local_prefixes.cdaweb]
pattern = "^[A-Z0-9_]+$"
uri_format = "https://cdaweb.gsfc.nasa.gov/misc/NotesO.html#$1"
why = "CDAS dataset ids (OMNI_HRO2_1MIN); DOIs resolve through doi; Bioregistry has no prefix (probes/cdaweb/13)"

[[routes]]
kind = "rest"
url = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys"
min_interval = 1.0
service = "spdf"
auth = "none"
reports_total = false

[[routes]]
kind = "query-endpoint"                # HAPI 2.0; see quarry-fadd26
url = "https://cdaweb.gsfc.nasa.gov/hapi"
min_interval = 1.0
service = "spdf"
auth = "none"
reports_total = false

[[routes]]
kind = "bulk"
url = "https://cdaweb.gsfc.nasa.gov/sp_phys/data"
min_interval = 1.0
service = "spdf"
auth = "none"
reports_total = false

[[routes]]
kind = "local-tool"
url = "sunpy.net.cdaweb"
min_interval = 0.0
service = "spdf"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "a time cut becomes a file only through a service-side job, at a temporary URL that differs per request"
handling = "files lists granules (orig_data); a cut is a request the adapter runs, never a stable URL"
evidence = "sun phase-1 survey probes/cdaweb/05, 19"

[[pitfalls]]
summary = "orig_data returns whole period granules overlapping the interval (a month for a day)"
handling = "every file carries its own coverage beside the asked interval"
evidence = "probes/cdaweb/04"

[[pitfalls]]
summary = "HAPI is a fixed grammar (id, parameters, time.min, time.max) streaming csv/json/binary, not TAP and not resource-shaped rest"
handling = "the adapter speaks the grammar; the route kind is provisional"
evidence = "probes/cdaweb/09, 10, 16"

[[pitfalls]]
summary = "no checksums on any route; LastModified and ETag only"
handling = "expected omitted; size and LastModified recorded"
evidence = "probes/cdaweb/14, 15"

[[pitfalls]]
summary = "the terms-of-use path the documentation implies is 404"
handling = "terms are read: open; the resource is used under SPDF's general rules"
evidence = "probes/cdaweb/12, 17"
```

```toml
# resources/soar.toml
id = "soar"
name = "Solar Orbiter Archive"
roles = ["repository"]
adapter = "quarry.adapters.soar"

[local_prefixes.soar]
pattern = "^solo_L[0-3]_[a-z0-9-]+_\\d{8}T\\d{6}\\d*(_V\\d+)?$"
uri_format = "https://soar.esac.esa.int/soar-sl-tap/data?retrieval_type=LAST_PRODUCT&data_item_id=$1&product_type=SCIENCE"
why = "SOAR data_item_id; Bioregistry has no prefix (probes/soar/07)"

[[routes]]
kind = "query-endpoint"                # TAP, ADQL 2.0/2.1
url = "https://soar.esac.esa.int/soar-sl-tap/tap"
min_interval = 1.0
service = "soar"
auth = "none"
reports_total = true                   # COUNT(*)

[[routes]]
kind = "rest"
url = "https://soar.esac.esa.int/soar-sl-tap/data"
min_interval = 1.0
service = "soar"
auth = "none"
reports_total = false

[[routes]]
kind = "local-tool"
url = "sunpy.net.soar"
min_interval = 0.0
service = "soar"
auth = "none"
reports_total = true

[[pitfalls]]
summary = "TAP_SCHEMA names carry the schema prefix (soar.v_sc_data_item) while FROM takes the bare view name"
handling = "prefix only in TAP_SCHEMA lookups"
evidence = "sun phase-1 survey probes/soar/02, 03, 05, 08"

[[pitfalls]]
summary = "no checksums in the views or on the download; filesize and Content-Length agree"
handling = "expected omitted; Content-Length checked against filesize"
evidence = "probes/soar/11, 14"

[[pitfalls]]
summary = "TAP_SCHEMA.columns has no units or descriptions for the data-item view"
handling = "column meaning from documentation, not the service"
evidence = "probes/soar/08"
```

```toml
# resources/soho.toml
id = "soho"
name = "SOHO archive (SDAC bulk tree)"
roles = ["repository"]
adapter = "quarry.adapters.soho"

[[routes]]
kind = "bulk"
url = "https://umbra.nascom.nasa.gov/pub"
min_interval = 1.0
service = "sdac"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "files carry no content-type and no digest"
handling = "type by extension; expected omitted"
evidence = "sun phase-1 survey probes/soho/11"

[[pitfalls]]
summary = "the archive is curated after the fact (deletions logged); three doors with different coverage"
handling = "every download is dated and hashed; search goes through vso"
evidence = "probes/soho/04, 13"

[[pitfalls]]
summary = "LASCO level-0.5 is organised by YYMMDD and the sample day's directory was not found"
handling = "read: open; EIT (lz/YYYY/MM) is the proven tree"
evidence = "probes/soho/09, 10, 12"
```

```toml
# resources/stereo-ssc.toml
id = "stereo-ssc"
name = "STEREO Science Center data tree"
roles = ["repository"]
adapter = "quarry.adapters.stereo_ssc"

[[routes]]
kind = "bulk"
url = "https://stereo-ssc.nascom.nasa.gov/data/ins_data"
min_interval = 1.0
service = "stereo-ssc"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "the all-days parent listing times out at 120 s"
handling = "list by day; never the parent"
evidence = "sun phase-1 survey probes/stereo-ssc/07"

[[pitfalls]]
summary = "file names are observation times to the second; a guessed name is 404"
handling = "list the day, then match"
evidence = "probes/stereo-ssc/01, 02"
```

```toml
# resources/helioviewer.toml
id = "helioviewer"
name = "Helioviewer API"
roles = ["repository"]                 # browse images with embedded FITS headers; metadata, not science data
adapter = "quarry.adapters.helioviewer"

[[routes]]
kind = "rest"
url = "https://api.helioviewer.org/v2"
min_interval = 1.0
service = "helioviewer"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "getClosestImage answers with the nearest image, which may lie outside the asked interval"
handling = "the returned date is compared with the request; --at compiles here, coverage does not"
evidence = "sun phase-1 survey probes/helioviewer/02"

[[pitfalls]]
summary = "an unknown image id is HTTP 500 with a JSON error, not 404"
handling = "errno 24 maps to not_found"
evidence = "probes/helioviewer/03"
```

### 4.2 Index series

```toml
# resources/goes-xrs.toml
id = "goes-xrs"
name = "GOES X-ray sensor data and flare lists (NOAA NCEI, SWPC)"
roles = ["repository"]
adapter = "quarry.adapters.goes_xrs"

[local_prefixes.goes]
pattern = "^g\\d{2}/[a-z0-9-]+_science/\\d{4}/\\d{2}/[a-z0-9_.-]+\\.nc$"
uri_format = "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/goes$1"
why = "NCEI L2 granule paths; Bioregistry has no prefix (probes/goes-xrs/08)"

[[routes]]
kind = "bulk"
url = "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes"
min_interval = 1.0
service = "ncei"
auth = "none"
reports_total = false

[[routes]]
kind = "rest"                          # rolling real-time windows; no records
url = "https://services.swpc.noaa.gov/json/goes/primary"
min_interval = 1.0
service = "swpc"
auth = "none"
reports_total = false

[[routes]]
kind = "local-tool"
url = "sunpy.net.dataretriever.sources.goes"
min_interval = 0.0
service = "ncei"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "file names carry a processing version (v2-2-1) that changes; a constructed name is 404"
handling = "list the month directory and match the day"
evidence = "sun phase-1 survey probes/goes-xrs/01, 02"

[[pitfalls]]
summary = "the SWPC JSON products are rolling windows, overwritten; not an archive"
handling = "no acquisition from them; NCEI granules are the record"
evidence = "probes/goes-xrs/04, 05"

[[pitfalls]]
summary = "the legacy flare event lists end in 2017; later flares are in L2 flsum granules"
handling = "two series under one id, split by epoch"
evidence = "probes/goes-xrs/03, 06"
```

```toml
# resources/silso.toml
id = "silso"
name = "WDC-SILSO sunspot number"
roles = ["repository"]
adapter = "quarry.adapters.silso"

[local_prefixes.silso]
pattern = "^SN_[dmy]s?_(tot|hem)_V\\d+\\.\\d+$"
uri_format = "https://www.sidc.be/SILSO/DATA/$1.txt"
why = "SILSO series file stems; Bioregistry has no prefix (probes/silso/07)"

[[routes]]
kind = "bulk"
url = "https://www.sidc.be/SILSO/DATA"
min_interval = 1.0
service = "sidc"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "one file per series, reissued monthly with the recent rows provisional"
handling = "a dataset is a dated release: the file, its manifest sha256 and Last-Modified"
evidence = "sun phase-1 survey probes/silso/02, 05, 06, 08"

[[pitfalls]]
summary = "missing values are -1; provisional rows carry '*'"
handling = "documented; never filled"
evidence = "probes/silso/05"

[[pitfalls]]
summary = "digests are published in manifest-paired.sha256 and verify (the one surveyed resource with digests)"
handling = "expected is taken from the manifest"
evidence = "probes/silso/08"
```

```toml
# resources/omniweb.toml
id = "omniweb"
name = "OMNIWeb and the SPDF OMNI bulk tree"
roles = ["repository"]
adapter = "quarry.adapters.omniweb"

[local_prefixes.omni]
pattern = "^omni_(min|5min|2|m)\\w*\\d{6}(\\.asc|\\.dat)?$"
uri_format = "https://spdf.gsfc.nasa.gov/pub/data/omni/high_res_omni/monthly_1min/$1"
why = "OMNI file names in the SPDF tree; the same data under cdaweb: dataset ids and a DOI (probes/omniweb/02, cdaweb/01)"

[[routes]]
kind = "bulk"
url = "https://spdf.gsfc.nasa.gov/pub/data/omni"
min_interval = 1.0
service = "spdf"
auth = "none"
reports_total = false

[[routes]]
kind = "rest"                          # the CGI form; returns the listing inside HTML
url = "https://omniweb.gsfc.nasa.gov/cgi/nx1.cgi"
min_interval = 1.0
service = "spdf"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "the CGI returns data inside an HTML page, not as a file"
handling = "described, not used for acquisition; the bulk tree and cdaweb are"
evidence = "sun phase-1 survey probes/omniweb/04"

[[pitfalls]]
summary = "the data set is revised in place as better source data arrive"
handling = "every download dated and hashed"
evidence = "probes/omniweb/01, 03"
```

### 4.3 Event knowledgebases

```toml
# resources/hek.toml
id = "hek"
name = "Heliophysics Events Knowledgebase"
roles = ["knowledgebase"]
adapter = "quarry.adapters.hek"

[local_prefixes.hek]
pattern = "^ivo://helio-informatics\\.org/[A-Za-z0-9_.:-]+$"
uri_format = "https://www.lmsal.com/hek/her?cmd=view-voevent&ivorn=$1"
why = "kb_archivid ivorns; Bioregistry has neither hek nor ivo (probes/hek/07, 08)"

[[routes]]
kind = "rest"
url = "https://www.lmsal.com/hek/her"
min_interval = 1.0
service = "lmsal"
auth = "none"
reports_total = false                  # overmax only

[[routes]]
kind = "local-tool"
url = "sunpy.net.hek"
min_interval = 0.0
service = "lmsal"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "interval search is by overlap and returns events that began before the interval"
handling = "event_starttime is returned with every record; the coverage filter documents overlap"
evidence = "sun phase-1 survey probes/hek/01"

[[pitfalls]]
summary = "no total count; overmax flags that more exist"
handling = "page until overmax is false or the cap is hit; coverage says limited"
evidence = "probes/hek/01, 02"

[[pitfalls]]
summary = "the spatial box is mandatory in the column search"
handling = "the adapter supplies the full-disk box by default"
evidence = "probes/hek/01, 03"

[[pitfalls]]
summary = "records carry scientific values (peak flux, GOES class, intensities)"
handling = "a read of a record is an exposure in the sun program's sense; quarry does not read for the user"
evidence = "probes/hek/03"
```

```toml
# resources/donki.toml
id = "donki"
name = "CCMC DONKI"
roles = ["knowledgebase"]
adapter = "quarry.adapters.donki"

[local_prefixes.donki]
pattern = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}-(FLR|CME|SEP|IPS|MPC|GST|RBE|HSS)-\\d{3}$|^\\d{8}-AL-\\d{3}$"
uri_format = "https://ccmc.gsfc.nasa.gov/DONKI-API/get/$1"
why = "flrID, activityID and messageID forms; no registry carries them"

[[routes]]
kind = "rest"
url = "https://ccmc.gsfc.nasa.gov/DONKI-API/get"
min_interval = 1.0
service = "ccmc"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "the API base moved on 2026-09-30; the old base answers HTTP 200 with an HTML page"
handling = "content-type is checked; non-JSON is upstream_failed"
evidence = "sun phase-1 survey probes/donki/01, 04"

[[pitfalls]]
summary = "records are revised (versionId) and link to each other (linkedEvents)"
handling = "versionId and submissionTime are kept with every record"
evidence = "probes/donki/07, 08, 09"
```

```toml
# resources/swpc-events.toml
id = "swpc-events"
name = "NOAA SWPC edited event lists and Solar Region Summaries"
roles = ["knowledgebase"]
adapter = "quarry.adapters.swpc_events"

[local_prefixes.noaa-ar]
pattern = "^\\d{4,5}$"
uri_format = "https://www.swpc.noaa.gov/products/solar-region-summary"
why = "NOAA active-region numbers, minted in the SRS and consumed by hek, donki and goes-xrs; the SRS prints them modulo 10000 (probes/swpc-events/03), the canonical form is the full number; Bioregistry has no NOAA space-weather scheme (probes/noaa-ar/03, 04)"

[[routes]]
kind = "bulk"
url = "ftp://ftp.swpc.noaa.gov/pub"
min_interval = 1.0
service = "swpc"
auth = "none"
reports_total = false

[[routes]]
kind = "rest"                          # today's products, rewritten in place
url = "https://services.swpc.noaa.gov/text"
min_interval = 1.0
service = "swpc"
auth = "none"
reports_total = false

[[routes]]
kind = "local-tool"
url = "sunpy.net.dataretriever.sources.noaa"
min_interval = 0.0
service = "swpc"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "past days live inside yearly tarballs; a day is not a URL"
handling = "the acquisition is the tarball; extraction is the consumer's"
evidence = "sun phase-1 survey probes/swpc-events/11"

[[pitfalls]]
summary = "the archive host is FTP only; https to it resets"
handling = "the bulk route is ftp://"
evidence = "probes/swpc-events/05, 09"

[[pitfalls]]
summary = "the last three days' lists are regenerated nightly"
handling = "downloads under 3 days old are provisional"
evidence = "probes/swpc-events/12"
```

```toml
# resources/cdaw-cme.toml
id = "cdaw-cme"
name = "SOHO LASCO CME catalog (CDAW, version 2)"
roles = ["knowledgebase"]
adapter = "quarry.adapters.cdaw_cme"

[[routes]]
kind = "bulk"
url = "https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver"
min_interval = 1.0
service = "cdaw"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "a CME is identified by (date, first-appearance time, central PA) and rows are edited a year or more after the event"
handling = "records are keyed by the triple and carry the file's Last-Modified"
evidence = "sun phase-1 survey probes/cdaw-cme/04, 06"

[[pitfalls]]
summary = "the catalog is incomplete by construction and has a separate data-gap list"
handling = "the resource description says so"
evidence = "probes/cdaw-cme/04"
```

### 4.4 Model, registry, vocabulary

```toml
# resources/hpde.toml
id = "hpde"
name = "HPDE SPASE metadata registry"
roles = ["knowledgebase"]              # misfit: a registry of resource descriptions; see quarry-bacd40
adapter = "quarry.adapters.hpde"

[local_prefixes.spase]
pattern = "^spase://[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$"
uri_format = "https://spase-metadata.org/$1.json"
why = "SPASE ResourceIDs, the field's one cross-resource identifier (cdaweb records carry them); Bioregistry has no prefix (probes/hpde/09)"

[[routes]]
kind = "rest"                          # landing pages: .json, .xml, .html per ResourceID
url = "https://spase-metadata.org"
min_interval = 1.0
service = "spase-metadata"
auth = "none"
reports_total = false

[[routes]]
kind = "bulk"                          # the registry's source: one git repository per naming authority
url = "https://raw.githubusercontent.com/hpde"
min_interval = 1.0
service = "github-raw"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "coverage dates in registry records lag the repository by years (OMNI StopDate 2020-01-01; CDAWeb 2026-07-08)"
handling = "coverage is never read from the registry"
evidence = "sun phase-1 survey probes/hpde/14; probes/cdaweb/01"

[[pitfalls]]
summary = "hpde.io may not resolve; the same records are served by spase-metadata.org and GitHub"
handling = "two routes to one record set"
evidence = "probes/hpde/01, 10, 11, 14"

[[pitfalls]]
summary = "records exist in several schema minor versions at once (2.7.0, 2.7.1)"
handling = "the adapter reads Version and tolerates minor versions"
evidence = "probes/hpde/14, 15"
```

```toml
# resources/spase.toml
id = "spase"
name = "SPASE data model and vocabularies"
roles = ["data-model", "vocabulary"]
adapter = "quarry.adapters.spase"

[[routes]]
kind = "bulk"
url = "https://spase-group.org/data/schema"
min_interval = 1.0
service = "spase-group"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "vocabularies (MeasurementType, ObservedRegion) are XSD enumerations; there is no term service"
handling = "terms are read from the versioned XSD (spase-2.7.2.xsd)"
evidence = "sun phase-1 survey probes/spase/04, 08"

[[pitfalls]]
summary = "documentation URLs move between versions; guessed paths 404"
handling = "only /data/model/ and the versioned XSD are cited"
evidence = "probes/spase/03, 06, 10, 11"
```

```toml
# resources/goes-class.toml
id = "goes-class"
name = "GOES soft X-ray flare class"
roles = ["vocabulary"]
adapter = "quarry.adapters.goes_class"   # an inline vocabulary: the terms live in the adapter

[[routes]]
kind = "bulk"                          # the definition page; nothing is fetched at run time
url = "https://www.swpc.noaa.gov/phenomena/solar-flares-radio-blackouts"
min_interval = 1.0
service = "swpc"
auth = "none"
reports_total = false

[[pitfalls]]
summary = "a class is a letter (A B C M X, decades of 0.1–0.8 nm peak flux from 1e-8 W/m2) plus an optional decimal multiplier, and the measuring satellite differs between lists"
handling = "pattern ^[ABCMX](\\d+(\\.\\d+)?)?$; the satellite stays with the record that carries the class"
evidence = "sun phase-1 survey probes/goes-class/01; probes/goes-xrs/05"
```

### 4.5 Literature

```toml
# resources/ads.toml
id = "ads"
name = "NASA Astrophysics Data System"
roles = ["literature-index"]
adapter = "quarry.adapters.ads"

[local_prefixes.bibcode]
pattern = "^\\d{4}[A-Za-z0-9.&]{14}[A-Z.]$"
uri_format = "https://ui.adsabs.harvard.edu/abs/$1"
why = "ADS bibcodes; Bioregistry has neither ads nor bibcode (probes/ads/05, 06)"

[[routes]]
kind = "rest"
url = "https://api.adsabs.harvard.edu/v1"
min_interval = 1.0
service = "ads"
auth = "required-key"
key_env = "ADS_DEV_KEY"
key_header = "Authorization"           # Bearer <token>
reports_total = true

[[pitfalls]]
summary = "every call needs a token; without one the answer is a JSON 401"
handling = "the static refusal precedes HTTP (quarry-e0f1c0)"
evidence = "sun phase-1 survey probes/ads/01"

[[pitfalls]]
summary = "the documentation is a client-side application; curl receives HTTP 202 with an empty body"
handling = "rate limits and terms are read: open until read in a browser"
evidence = "probes/ads/02, 03"
```

`arxiv` is a `core` resource (Bioregistry `arxiv`; `probes/arxiv/05`); the domain adds
only its default category. Its file is `core`'s to write; the survey's evidence for it:
a 3-second interval requested in prose (`probes/arxiv/03`), date ranges on
`submittedDate` and `lastUpdatedDate` that are exactly quarry's shared `--since` and
`--until` (`probes/arxiv/06`), brackets that must be URL-encoded (`probes/arxiv/02`), and
a feed-level `<title>` that is the query echo (`probes/arxiv/01`).

### 4.6 Not resources

`noaa-ar` is the `noaa-ar` local prefix on `swpc-events` (its minter). `sunpy` is the
`local-tool` route on the resources it reaches (`probes/sunpy/01`), not a resource.
`kp-dst` is out of scope.

## 5. Domain filters and their compilation

| filter | takes | compiled to | by |
|---|---|---|---|
| `--coverage <start>/<end>` | an ISO interval (observation time); `--at <instant>` is the nearest-instant form | `T_REC` range in the record-set specifier, written `_UTC` | jsoc |
| | | `startDate`/`stopDate` (dataset selection), `orig_data/<t0>,<t1>` (granules), HAPI `time.min`/`time.max` | cdaweb |
| | | `begin_time >= … AND begin_time < …` in ADQL | soar |
| | | `time/start`, `time/end` in the SOAP query | vso |
| | | `event_starttime`/`event_endtime` (overlap: events that began before the interval are returned, and the coverage entry says so) | hek |
| | | `startDate`/`endDate` | donki |
| | | the directory path (year, month, day) and file-name matching | soho, stereo-ssc, goes-xrs, silso (whole file: client-side), omniweb, swpc-events (year tarball), cdaw-cme (month file) |
| | `--at` only | `getClosestImage?date=`; `--coverage` is refused (`unsupported_filter`) | helioviewer |
| `--instrument <name>` | an instrument name (AIA, HMI, EUI, XRS, EIT, EUVI, …) | series prefix (jsoc); `instrument=` (cdaweb, soar, vso, hek); directory (soho, stereo-ssc); `sourceId` lookup (helioviewer) | the archives and hek |
| `--observatory <name>` | SDO, SOHO, Solar Orbiter, STEREO-A, GOES-18, … | `observatoryGroup` (cdaweb); `source` (vso); `obs_observatory` (hek); path (bulk trees) | as above |
| `--measurement <term>` | a SPASE `MeasurementType` term (`MagneticField`, `ThermalPlasma`, …) | `instrumentType` (cdaweb, approximately); `physobs` (vso, approximately); refused elsewhere | cdaweb, vso |

Rules the spec already has, applied here: filters combine with AND; an adapter that
cannot express one refuses with `unsupported_filter`; `--native` passes a resource's own
grammar (a JSOC record-set specifier, an ADQL `WHERE`, a HEK column set) with exactly one
`--in`. `--since`/`--until` keep their shared meaning (release date) and compile where a
resource has one: `[? DATE > $(…) ?]` (jsoc), `insertion_time` (soar), `submittedDate`
(arxiv), `pubdate` (ads); refused elsewhere.

## 6. The `quarry-heliophysics` skill, hand-written part

Which resource answers which question (from the dossiers' `fit` and disposition lines):

- *Which images exist of the Sun at a time?* `vso` (across archives), then `jsoc` (SDO),
  `soar` (Solar Orbiter), `soho` and `stereo-ssc` (bulk trees), `helioviewer` (browse
  only, nearest instant).
- *What was the solar wind or an index doing?* `cdaweb` (in situ and OMNI, with HAPI
  for cuts), `omniweb` (OMNI files), `goes-xrs` (X-ray flux granules), `silso` (sunspot
  number, one file).
- *What events were reported?* `hek` (detections and reports, by overlap), `donki`
  (curated space-weather events and notifications), `swpc-events` (forecaster lists and
  the SRS, by day), `cdaw-cme` (LASCO CMEs, by month).
- *What is this dataset, instrument or id?* `hpde` (SPASE descriptions; never for
  coverage), `spase` (the terms), `goes-class` and the `noaa-ar` prefix (the labels).
- *What was published?* `arxiv` (`astro-ph.SR` by default), `ads` (with a key).

And what to expect: a time cut is a request, not a file, on `cdaweb` and `soar`, and a
record set on `jsoc`; digests exist only at `silso`; coverage dates from `hpde` are
stale; `donki`'s base moved once and may again.

## 7. Extensions this domain asks for

Each filed, each allowed by the quarry spec's §14, none changing a verb:

1. **An acquisition-request field for the cut** (`quarry-b794e6`): interval and the
   resource's selection parameters beside the locator; the adapter resolves URLs at
   request time. Until it lands, `files --acquisition` on a series resource names the
   granules (cdaweb `orig_data`, a JSOC record set's current paths) and the cut lives in
   the title.
2. **The `coverage` domain filter** (`quarry-bc633c`), §5, with its overlap and
   nearest-instant semantics stated.
3. **HAPI** (`quarry-fadd26`): a route kind or a documented `rest` grammar; written as
   `query-endpoint` here.
4. **A registry role** (`quarry-bacd40`) for `hpde` and `vso`; written as
   `knowledgebase` and `repository` here, each with the misfit in a pitfall.
5. **A route liveness check** (`quarry-e2c1f1`) beside `quarry check`'s shape checks.

## 8. What `quarry check` says about this domain today

Loading these files would pass the shape checks (`RESOURCE_KEYS`, `ROUTE_KEYS`, auth
values, `key_env` with a key location). `check` would report:

- `missing_adapter` for every resource in §4: the implementation task's work;
- nothing for `local_prefixes`: none of `jsoc`, `cdaweb`, `soar`, `goes`, `silso`,
  `omni`, `hek`, `donki`, `noaa-ar`, `spase`, `bibcode` is a Bioregistry prefix or
  synonym (`probes/goes-class/03`, `04`: Bioregistry's search for solar and
  heliophysics terms is empty), and each is lowercase;
- `prefix_owned_twice`: none; OMNI's ownership is assigned in §2;
- `search_default_outside`: none.

## 9. Open questions

1. Whether `arxiv` listed in both `core` and this domain is how a domain declares a
   default category, or the domain file wants a field for per-resource defaults.
2. Whether `local-tool` routes name a Python module in `url`, as written here, or want
   their own key.
3. The `measurement` filter: SPASE terms compile approximately to CDAWeb's instrument
   types and VSO's `physobs`; whether an approximate compilation is `"match":
   "expanded"` or a refusal.
4. Terms of use for most resources were not found by probe (survey README, question 2)
   and should be read before the first acquisition.
