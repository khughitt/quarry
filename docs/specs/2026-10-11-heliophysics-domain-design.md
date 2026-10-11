# quarry: the heliophysics domain

Status: draft 2026-10-11, revised after review rounds 1 and 2 (codex/gpt-6-astra,
relayed by the user; round 2 accepted the catalog corrections); awaiting the user's
approval for planning. Written from the sun program's phase-1 resource
survey (sun repository, `docs/reports/2026-10-10-phase-1-survey/`, task `sun-374ea3`),
against the approved quarry spec (`2026-10-10-quarry-domain-layers-design.md`) and the
catalog as implemented in `src/quarry/catalog.py` on 2026-10-11. Source goal
`sci-ba7150`. The extensions this domain asks for are filed as `quarry-bc633c`, `quarry-bacd40`
and `quarry-e2c1f1`, with `quarry-b794e6` as a consideration; this spec records how the
domain is written while they are undecided. Every TOML block below was loaded with
quarry's own loader, checker and identifier resolver on 2026-10-11 (§8).

Sources: the survey's dossiers (`dossiers/<id>.md`, one per resource, each ending in the
draft TOML copied here) and probe logs (`probes/<id>/log.md`, cited as `probes/<id>/<n>`
in every `evidence` field); the quarry spec §4 (model), §6 (catalog format), §7
(identifiers), §8.2 (filters), §9 (acquisition), §14 (other domains).

## 1. What this is

The first quarry domain outside biology and the first test of the spec's §14: the Sun
and the solar wind's data resources as a lens over one CLI. Twenty-one resources,
nineteen with a resource file here (`arxiv` included, since `core` has none yet;
`noaa-ar` is a local prefix on `swpc-events` and `sunpy` is a set of `local-tool`
routes); four domain filters; eight pitfall families observed live.
Magnetospheric and ionospheric resources (Kp, Dst) are out of scope through the sun
program's phase 3 and are not listed.

What it is not: adapters. Every resource file names an adapter module that does not
exist yet; `quarry check` reports `missing_adapter` for each until the implementation
task builds it, and this spec says what each adapter compiles (§5). Nor does it change
the verbs: the extensions in §7 are a domain filter, a
role and a check, each allowed by §14, with a request field as a consideration.

## 2. Decisions

| Decision | Chosen | Rejected |
|---|---|---|
| Domain name | `heliophysics` (the science's name, as `biology`) | `solar`, `sun` (the program's repository name, not a science) |
| Includes | `core` (Zenodo, figshare, PubMed) | a `physics` layer: nothing surveyed needs it, and quarry's §14 physics row (HEPData, INSPIRE) shares no resource with this domain |
| Literature | `arxiv` declared here (its file in §4.5) and proposed for `core`, which has no arXiv resource today; `ads` listed in this domain | `ads` in `core`: it is the astrophysics index, keyed, and no other domain asked for it |
| Beliefs contract | none declared | `beliefs_contract = "heliophysics"`: no such contract exists, and the sun framing decides the contract by a worked example in its phase 3, not here |
| Time | a domain filter `coverage` (observation interval), with `--at` for an instant | a shared coverage filter: one time-indexed domain is not evidence for the semantics a second will want (quarry-bc633c) |
| Identifiers | `local_prefixes` for every scheme but `arxiv` and `doi` | waiting for Bioregistry: its search for "solar" and "helio" is empty (probes/goes-class/03, 04) |
| OMNI ownership | `cdaweb` owns `cdaweb:` dataset ids and serves OMNI; `omniweb` owns the SPDF file tree under `omni:` | one OMNI resource: it has three doors with different retrieval units |
| Registry and index | `hpde` and `vso` carry `knowledgebase` and `repository` with the misfit in a pitfall | inventing a role here: that is quarry-bacd40's decision |
| HAPI | `rest`, with the grammar in the adapter and a pitfall | a `hapi` kind: HAPI is REST endpoints (`/catalog`, `/info`, `/data`) with one stable GET URL per cut (survey `probes/cdaweb/16`); the kind fits as written |

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

`arxiv` is listed here because `core` has no arXiv resource today (`core.toml`: zenodo,
figshare, pubmed); its file is in §4.5 and the proposal is that `core` adopts it, after
which this domain's entry becomes the default-category carrier only. No
`beliefs_contract`, no `facet_namespaces` (§2).

**The effective default search set is five, not three**: the three named here plus
Zenodo and figshare inherited from `core` (loader output, §8: `vso, cdaweb, hek,
zenodo, figshare`), exactly as a biology search covers GEO, ENA, BioStudies, Zenodo and
figshare (quarry spec §6). That is intended: Zenodo and figshare hold solar datasets
deposited beside papers. The domain's own three are few because `vso` already
federates JSOC, SDAC and NSO, `soar` is a per-mission archive, the bulk trees have no
search, and the knowledgebases other than HEK answer narrower questions. A user names
the rest with `--in`.

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
summary = "the export route refuses without a registered email, is asynchronous (exp_request then exp_status until status 0), and completed with an empty file list for a cut rs_list resolves to 10 records; no digests on any route"
handling = "required-key route; acquisition prefers rs_list with R=1 (online SUMS paths) over export; expected omitted; sizes by HEAD"
evidence = "probes/jsoc/05, 18, 20, 21, 22, 11"
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
kind = "rest"                          # HAPI 2.0: /catalog, /info?id=, /data?id=&parameters=&time.min=&time.max=&format=
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
handling = "files lists granules (orig_data); a cut is emitted as a HAPI URL, never as the REST job's temporary URL"
evidence = "sun phase-1 survey probes/cdaweb/05, 19"

[[pitfalls]]
summary = "orig_data returns whole period granules overlapping the interval (a month for a day)"
handling = "every file carries its own coverage beside the asked interval"
evidence = "probes/cdaweb/04"

[[pitfalls]]
summary = "HAPI /data is one stable GET URL per cut (id, parameters, time.min, time.max, format), streamed with a header; the only CDAWeb route whose cut URL is reproducible"
handling = "files --acquisition for a cut emits the HAPI URL; the REST data job is never cited as a URL"
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
pattern = "^goes\\d{2}/l2/data/[a-z0-9-]+(_science)?/\\d{4}/\\d{2}/[a-z0-9_.-]+\\.nc$"
uri_format = "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/$1"
why = "NCEI L2 granule paths below /goes/ (goes18/l2/data/xrsf-l2-avg1m_science/2025/10/sci_….nc, probes/goes-xrs/09); Bioregistry has no prefix (probes/goes-xrs/08)"

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
uri_format = "https://ccmc.gsfc.nasa.gov/tools/DONKI/"
why = "flrID, activityID and messageID forms; no per-id endpoint was observed (records are fetched by type and date window, probes/donki/07–09), so resolution lands on the DONKI application; no registry carries them"

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
kind = "bulk"                          # NCEI's HTTPS mirror of the SWPC daily products, one file per day: the acquisition route
url = "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports"
min_interval = 1.0
service = "ncei"
auth = "none"
reports_total = false

[[routes]]
kind = "bulk"                          # the SWPC archive itself: FTP, yearly tarballs; not reachable by the kernel's HTTP(S) acquisition
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
summary = "the SWPC archive is FTP only (https resets) and past days live inside yearly tarballs; the kernel's acquisition speaks HTTP(S)"
handling = "acquisition uses NCEI's HTTPS mirror, one file per day (solar_region_summaries/YYYY/MM/YYYYMMDDSRS.txt, solar_event_reports/YYYY/MM/YYYYMMDDevents.txt); the FTP route is described for completeness"
evidence = "sun phase-1 survey probes/swpc-events/05, 09, 11, 14, 15, 17, 18"

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
pattern = "^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$"
uri_format = "https://spase-metadata.org/$1.json"
why = "SPASE ResourceIDs written as CURIEs without their scheme: spase://NASA/NumericalData/OMNI/… is spase:NASA/NumericalData/OMNI/…, and the landing URL is the path plus .json (probes/hpde/14); the field's one cross-resource identifier (cdaweb records carry it); Bioregistry has no prefix (probes/hpde/09)"

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

```toml
# resources/arxiv.toml  — proposed for core; declared by this domain until core adopts it
id = "arxiv"
name = "arXiv"
roles = ["literature-index"]
prefixes = ["arxiv"]                   # Bioregistry's prefix (probes/arxiv/05)
adapter = "quarry.adapters.arxiv"

[[routes]]
kind = "rest"                          # Atom over GET; search_query, id_list, start, max_results
url = "http://export.arxiv.org/api/query"
min_interval = 3.0                     # 'incorporate a 3 second delay' (the API user manual)
service = "arxiv"
auth = "none"
reports_total = true                   # opensearch:totalResults

[[pitfalls]]
summary = "date ranges (submittedDate, lastUpdatedDate) use brackets that must be URL-encoded; a literal [ fails the request"
handling = "the adapter encodes; --since/--until compile to submittedDate"
evidence = "sun phase-1 survey probes/arxiv/02, 06"

[[pitfalls]]
summary = "the feed-level <title> is the query echo, not a record"
handling = "entries only"
evidence = "probes/arxiv/01, 06"

[[pitfalls]]
summary = "metadata is CC0; e-prints carry their own licences"
handling = "records carry the licence field when present"
evidence = "probes/arxiv/04"
```

The heliophysics domain adds `astro-ph.SR` as the default category (an adapter setting
the domain passes; where that setting lives is §9 question 1).

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

1. **The `coverage` domain filter** (`quarry-bc633c`), §5, with its overlap and
   nearest-instant semantics stated.
2. **A registry role** (`quarry-bacd40`) for `hpde` and `vso`; written as
   `knowledgebase` and `repository` here, each with the misfit in a pitfall.
3. **A route liveness check** (`quarry-e2c1f1`) beside `quarry check`'s shape checks.

And one consideration, not an extension the domain depends on: **a structured cut field
in the acquisition request** (`quarry-b794e6`, lowered after the survey's review round
1). As written, `files --acquisition` on a series resource emits the cut inside the
locator (a JSOC record-set specifier) or inside a URL (a HAPI `/data` URL), both
reproducible from the record; a field would make the cut legible to the kernel. HAPI
needs no route kind: it is `rest` (survey finding withdrawn, `quarry-fadd26` dropped).

## 8. What quarry's loader, checker and resolver say about this domain

Run 2026-10-11 against this spec's TOML blocks copied into a temporary catalog beside
the live `core` and `biology` files (`quarry.catalog.load`, `check` with no adapter
modules, `quarry.ids.Registry.load().with_local`):

- loading passes: every file is in shape (`RESOURCE_KEYS`, `ROUTE_KEYS`, auth values,
  `key_env` with a key location, `local_prefixes` tables);
- `check`: `missing_adapter` for each of the 19 resources with a new adapter (the
  implementation task's work) and nothing else after this revision; the first draft also
  gave `missing_resource: arxiv`, fixed by §4.5;
- effective resources: the 19 here plus `zenodo`, `figshare`, `pubmed` from `core`;
  search set `vso, cdaweb, hek, zenodo, figshare`; filters `coverage, instrument,
  measurement, observatory`;
- `local_prefixes`: none collides with a Bioregistry prefix or synonym, each is
  lowercase;
- the resolver, on representative ids: `jsoc:aia.lev1_euv_12s[…]` →
  `jsoc_info?op=rs_list&ds=…`; `cdaweb:OMNI_HRO2_1MIN` → the NotesO anchor; `soar:solo_L2_…`
  → the download route; `goes:goes18/l2/data/xrsf-l2-avg1m_science/2025/10/sci_….nc` →
  the NCEI file (the first draft produced `goesg18` and dropped `l2/data`);
  `silso:SN_d_tot_V2.0` → the TXT; `omni:omni_min202510.asc` → the SPDF file;
  `hek:ivo://…` → the HER view; `spase:NASA/NumericalData/OMNI/…/PT1M` →
  `spase-metadata.org/NASA/…/PT1M.json` (the first draft put `spase://` in the path);
  `noaa-ar:14545` → the SRS page; `bibcode:2012SoPh..275...17L` → ADS; `arxiv:2108.02624v2`
  → arXiv; `donki:…` → the DONKI application (no per-id endpoint exists).

The run is reproducible from the spec: extract the ```toml blocks whose first line names
`resources/<id>.toml` into a catalog directory and load it.

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
