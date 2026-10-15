# Research Findings - Multi-State SOR Integration

Research pass 1. Date: 2026-10-15. Method: web search + page fetches. Feeds the private plan.

Confidence tags: `[FETCHED]` read from the page itself, `[SNIPPET]` only seen in search result text, `[UNVERIFIED]` vendor claim or my inference, `[FAILED]` page could not be loaded.

Rule for this doc: every fact has a source link. Nothing here comes from memory. Anything I could not confirm says so.

---

## 1. Headline findings (read this first)

1. **NSOPW has no public API and bans automated searching.** Conditions of use and a Sept 2023 suspension notice both say so. An "NSOPW connector" cannot be a live automated integration.
2. **Iowa publishes a documented public search API (no key, 50 requests per hour). I found none elsewhere.** Other states give 3 shapes: live website search only (no API), bulk file downloads (TX, FL, HI, AR), GIS/open-data services (TN, DC), plus commercial vendor APIs.
3. **Public registries are not complete.** Iowa and Texas both say their public site does not list everyone. "No record found" does not mean "not an offender". Texas says only fingerprints positively identify a person.
4. **Use is legally constrained.** Registry data comes with anti-harassment terms. California bars employment use. Kansas says its site is not a valid background check. Patient admission use is a different case from employment, but needs legal review.
5. **Some states require registry checks at admission** (Maryland, Ohio confirmed in sources). That is the real driver for this project.
6. **Vendor APIs exist** and give concrete shapes to model our vendor adapter on (sync POST and async order-then-poll).
7. **HIPAA sets no log retention number.** The common 6 years comes from documentation rules, by interpretation.
8. **No authoritative Jaro-Winkler threshold exists.** Must calibrate on our own labeled data. Name-only matching is considered too aggressive.

Impact on plan: provider taxonomy changes (add `snapshot`), NSOPW becomes mock-only, matching gets a `disclaimer`/`dataAsOf` response contract, legal review becomes an explicit gate. See the private plan section 12 (ADR-017 to ADR-020).

---

## 2. NSOPW (national site)

| Fact | Detail | Confidence | Source |
|------|--------|-----------|--------|
| Conditions of use | Users agree not to do "automated searching or attempts to bypass security mechanisms". Permitted use is learning about offenders in your community. Disclaimer of accuracy, must verify with jurisdiction | FETCHED | [NSOPW Conditions](https://nsopw.gov/en/conditions) |
| Enforcement | Notice created Sept 8, 2023: automated process detected, "Automated searching of NSOPW is strictly prohibited", system suspended until activity is in acceptable range. No stated alternative for organizations | FETCHED | [NSOPW suspended notice](https://www.nsopw.gov/suspended) |
| Organizational accounts | Exist. Account holders skip CAPTCHA after 5 searches. Throttles protect jurisdictions. Bypassing throttles can disable the account and the organization's accounts. No API or batch search described | FETCHED | [Organizations conditions](https://organizations.nsopw.gov/Conditions) |
| Employment/housing | Conditions page does not mention these uses | FETCHED | [NSOPW Conditions](https://nsopw.gov/en/conditions) |
| Coverage | 50 states, DC, 5 territories (American Samoa, CNMI, Guam, Puerto Rico, US Virgin Islands) and 151 tribes participate. About 56 non-tribal jurisdictions | SNIPPET (FY25 SMART grant listing) | [SMART FY25 NSOPW grant](https://highergov.com/grant-opportunity/smart-fy25-maintenance-and-operation-of-the-dru-sjodin-national-sex-offender-pub-358083) |
| Statute | NSOPW is established by federal law, query form and limits set by Attorney General | SNIPPET | [34 USC 20920](https://www.govinfo.gov/link/uscode/34/20920) |
| Third-party scrapers | Apify actors advertise NSOPW scraping. Unofficial, not affiliated, and conflicts with the terms above. Do not use | SNIPPET | [Apify NSOPW actor](https://apify.com/dromb/nsopw-offender-search) |

**Consequence:** `nsopw` provider in our design = mock only. Real use would need written permission from the DOJ SMART Office. Add as an open question, not a build target.

---

## 3. State registries - what access actually looks like

Searched: IA, TX, FL, CA, KS, HI, AR, TN, DC. Others (NY, VA, WA, OR, UT etc.) returned nothing useful, so the 50-state matrix still needs the manual pass (R2).

### 3.1 Iowa (our reference state)
- Registry under Iowa Code Chapter 692A, public site `iowasexoffender.gov`. FAQ says site updates daily, as often as every 30 minutes Monday to Friday, based on law enforcement information. [FETCHED via search snippet] [Iowa FAQ](https://www.iowasexoffender.gov/faq/content)
- **Public site does not list every registered offender** (Iowa Code 692A.13 carve-out). Conditions: anti-harassment warning, error reports go to county sheriff. Nothing on automated access or employment. [FETCHED] [Iowa conditions on NSOPW](https://organizations.nsopw.gov/Conditions/JurisdictionConditions/IA)
- Open data catalog lists an "Iowa Sex Offender Registry" dataset, publisher Iowa DPS, id `qeyj-xvz9` on data.iowa.gov, first published 2015-03-27, last modified 2023-08-30, "non-federal" terms, no license info. [SNIPPET] [data.gov entry](https://catalog.data.gov/dataset/iowa-sex-offender-registry)
- **Official API exists** (correction of my first pass). Guide at `/feed/info`. [FETCHED] [Iowa API guide](https://www.iowasexoffender.gov/feed/info)
  - Base `https://www.iowasexoffender.gov`. Search: `/api/search/results.json` (also `.xml`, `.rss`). Record: `/api/registrant/{id}.json`. No auth.
  - Search params: `lastname`, `firstname` (starts-with), `alias=1`, `postalcode`, `city`, `countyname`, `age`, `gender`, `updated=today`, paging `per_page`/`page`, `sort`/`order`. Max 100 results per request.
  - Fields: registrant id, names, birthdate, gender, address, city, county, postal_code, tier, victim flags, photo, last_changed. Convictions, aliases, markings only when 25 or fewer results per page.
  - **Limits:** no more than 50 requests per hour, 10 second limit per request, errors if too many results.
  - **Caching:** allowed, but cached data must not be held longer than 14 days. Lat/lon must not be cached or reused beyond immediate map display.
  - Error shape: `results:0, records:null, error:1, message`.
  - Terms: warranty disclaimer, using the API means accepting them.
- Site shows 7,196 active registrants, last update 2026-10-13, typically updated hourly on weekdays. [FETCHED] [Iowa home](https://www.iowasexoffender.gov/)
- **Live-tested 2026-10-15 (about 25 requests, shapes and counts only, no personal data kept):**
  - Birthdate and last_changed come as `MM/DD/YYYY`. `state` comes as a full word ("Iowa", "Nebraska"), not a code. Names with apostrophes: `lastname=OBRIEN` finds people, `O'BRIEN` finds none, so dropping apostrophes is right for Iowa.
  - `/api/registrant/{id}.json` returns the record at top level with aliases, 1 to 3 seconds each. The public detail page `/registrant/registrantdetail?id=` returns 200.
  - Latency: `per_page=100` about 2s with no aliases. `per_page=25` about 8s with aliases (server builds extra fields), against a 10s server limit.
  - Aliases matter: for `lastname=SMITH` (87 results) 28 matched only through an alias, and none of the 87 carried alias data on the 100-per-page response.
  - Narrowing by first initial keeps results to 25 or fewer: SMITH + J = 15 results, + R = 10, + B = 7, each in one page with aliases, 2 to 5 seconds.
- data.iowa.gov dataset `qeyj-xvz9` still 404s on the API paths I tried. A search summary says it offers CSV/XML/Excel, unconfirmed. Use the official API instead.
- This is very likely the API the client's existing Iowa integration uses. Still worth getting their code for their conventions.
- A vendor case study says registry data flows from the state registry unit to the public site via NCIC and the state CIO office. [SNIPPET] [Zirous case study](https://www.zirous.com/case-study/modernizing-iowas-sex-offender-registry/)

### 3.2 Texas (DPS)
- Public search site plus a **bulk database export**: about 15 MB, updated twice a week, free, needs a TxDPS Public Website Account. Docs and SQL Server import scripts included. Format and field list not stated on the page. [FETCHED] [TX export page](https://sor.dps.texas.gov/Home/Export)
- DPS warns: not all offenders are on the public database, and only fingerprint verification positively links a person to a record. Misuse to injure or harass can bring prosecution. [SNIPPET] [TX registry](https://sor.dps.texas.gov/)
- Social network operators can request a version with online identifiers (separate path). [FETCHED]
- No API documented.

### 3.3 Florida (FDLE)
- CSV "Registration List Data File", statewide or by county. Portal asks for an email so FDLE can contact you about bad data. Separate employment-address file, memo says updated every 4 hours. The memo describes URLs that trigger automatic downloads with email and county parameters, so scripted pulls look possible (my inference, not documented as an API). [SNIPPET] [FDLE data file](https://www.fdle.state.fl.us/SOPS/public_data_file), [portal](https://offender.fdle.state.fl.us/offender/publicDataFile.jsf)
- FDLE recommends redistributors include two warnings: no positive ID without fingerprints, and no use to commit crimes or harass. [SNIPPET]
- Direct fetch of the portal and the FDLE page failed (connection refused / 404). [FAILED] Re-check by browser.

### 3.4 California (Megan's Law)
- Statutory public database at `meganslaw.ca.gov` (Penal Code 290.4 mandates it). No API or bulk download found. [SNIPPET] [UC Davis PD page](https://police.ucdavis.edu/professional-standards/megans-law)
- A law-firm article says California law expressly prohibits using the registry information for employment, with exceptions, and misuse carries damages and fines. Verify against Penal Code 290.46 directly. [SNIPPET, secondary] [Littler](https://www.littler.com/publication-press/publication/californias-new-megans-law-website-employers-are-cautioned-not-make)
- Old community scrapers exist and are broken. Do not build on them.
- **Verdict for us:** California = vendor or mock only until legal says otherwise.

### 3.5 Hawaii
- Official bulk download system. Data: names, aliases, birth year, descriptions, photo, addresses, vehicles, employment street, conviction info. $100 per download, login required (cost from a search snippet). Anti-harassment terms. [FETCHED + SNIPPET] [HI bulk data](https://hcjdc.ehawaii.gov/bulkcor/public/about.html)

### 3.6 Arkansas
- Bulk file via Information Network of Arkansas: paid annual subscription, $0.10 per record enhanced access fee, FTP delivery, unauthorized use is prosecutable. [SNIPPET] [Arkansas data download](https://portal.arkansas.gov/service/ar-sex-offender-registry-data-download/)
- The Ark.org page itself only showed a CAPTCHA gate. [FETCHED, no detail]

### 3.7 Kansas
- KBI web registry, updated every 15 minutes, searchable by name, location, offender type, phone, online identity. States it does not contain full criminal history and is **not a valid background check for employment or housing**. No API found. [SNIPPET] [KBI registry](https://www.kansas.gov/kbi/ro.shtml)

### 3.8 GIS and open-data style access
- **Tennessee:** TBI registry published as an ArcGIS MapServer (geocoded points, nightly updates). Field list, query rights and terms not read. [SNIPPET] [TN MapServer](https://tnmap.tn.gov/ArcGIS/rest/services/PUBLIC_SAFETY/TBI_SEX_OFFENDER_REGISTRY/MapServer/info/iteminfo?f=pjson). Fetch failed with connection reset. [FAILED]
- **District of Columbia:** CSOSA registry on Data.gov with ArcGIS REST, CSV, GeoJSON, KML, shapefile, Creative Commons Attribution license, block-level locations. [SNIPPET] [DC dataset](https://catalog.data.gov/dataset/sex-offender-registry)

### 3.9 What this means for the matrix (R2)
Four access archetypes. Each state row should be tagged with one:

| Archetype | Examples seen | How an adapter works |
|-----------|---------------|----------------------|
| `live_api` | IA (documented, 50 req/hr) | HTTP call per check, needs quota limiter and short-lived cache |
| `open_data` / GIS | DC, TN, (IA unconfirmed) | Query ArcGIS/Socrata endpoint. Often geocoded, name search may be limited |
| `snapshot` | TX, FL, HI, AR | Scheduled download, load into local index, search locally |
| `vendor_only` / `none` | CA (probably), most others | Use vendor API |

Registry software vendors are a reuse opportunity: OffenderWatch claims to power the state registry in 16 states and local registries in 38 (vendor claim). If sites share software, one adapter family may cover several states. [UNVERIFIED] [OffenderWatch network](https://offenderwatch.com/national-sex-offender-registry)

---

## 4. Vendor and aggregator APIs (models for our vendor adapter)

| Vendor | Shape | Notes | Confidence | Source |
|--------|-------|-------|-----------|--------|
| IDScan.net | `POST https://screening.idscan.net/api/Check`, Bearer token. Required: firstName, lastName, dateOfBirths. Optional: middleName, sex, ssn, address fields, `services[]` (SOR service id), `referenceId`. Response: array of service results with `success`, `error`, `profiles[]`. Empty `profiles` + `success:true` = no match. Profile has fullName, dateOfBirths, address, aliases, offenses[], photoUrl, source. Claims real-time screening for all 50 states. Match logic undocumented | Sync, one call | FETCHED | [IDScan docs](https://docs.idscan.net/screening/sex-offender/sex-offender.html) |
| Certn | "US National Sex Offender Registry" check over 4,000 law enforcement databases using name, DOB, SSN. Order returns `201` with `id`, then `GET /api/public/cases/{id}/` for results. Errors 400/401/403/422. Sync vs async and webhooks not stated | Async order then poll | FETCHED | [Certn API](https://centric-api-docs.certn.co/reference/check-US_NATIONAL_SEX_OFFENDER_REGISTRY_1.md) |
| Offendersearch | REST (API key, JSON) and MCP server. Claims 58 registries, batch up to 1,000 lookups per call, returns match confidence, match basis, per-source citation with last-checked timestamp. 25 free searches. States results are public-record data, **not a consumer report** | Sync + batch | SNIPPET, UNVERIFIED | [Offendersearch MCP listing](https://mcpservers.org/servers/offendersearch/mcp) (fetch returned 403) |
| Nannostomus, Zyla "Offender Registry USA" | Marketing-level only. Claims of 771k+ records, free tiers. No provenance details | | SNIPPET, UNVERIFIED | [Zyla listing](https://www.zylalabs.com/api-marketplace/data/offender+registry+usa+api/2117) |

Design implications:
- Vendor adapter must support **sync** and **async (order, poll, timeout)** styles behind one interface.
- Vendor responses already return `profiles[]` with aliases and offenses, so our `RegistryRecord` shape is on the right track.
- A vendor-provided `last checked` timestamp is useful. Add `sourceAsOf` to the record.
- Vendors give no documented matching logic. Our matcher scoring them ourselves is the right call (plan ADR-005).
- Check each vendor's permitted-use and FCRA position before signing anything. Offendersearch explicitly says not a consumer report.

---

## 5. Legal and regulatory

| Topic | Finding | Confidence | Source |
|-------|---------|-----------|--------|
| Admission checks required by state law | Maryland: registrants must disclose status when applying to nursing homes or assisted living, facilities must check the state registry, facility not liable for actions based on registry info. Ohio requires pre-admission screening plus a care plan. Pennsylvania lawmaker said at least 10 states impose requirements. Connecticut 2022 bill replaced by a task force after opposition | SNIPPET | [Maryland HB 1072](https://mgaleg.maryland.gov/2014RS/fnotes/bil_0002/hb1072.pdf), [Matzie PA release](https://pahouse.com/InTheNews/NewsRelease/?id=122707), [CT article](https://www.pressreader.com/usa/new-haven-register/20220425/281612423961431) |
| Federal CMS rule | None found in these results. Would live in 42 CFR Part 483 | SNIPPET (negative) | |
| FCRA | Applies in full when a consumer reporting agency (CRA) compiles the report. Adverse action steps apply (pre-adverse notice, waiting period, final notice). Mostly about employment screening, so likely not our case, but a vendor acting as a CRA would matter | SNIPPET, vendor blogs | [GCheck](https://gcheck.com/blog/fcra-sex-offender-registry-check/) |
| State use limits | State law governs what is public and how it may be used. California Penal Code 290.4(d) quoted as barring employment use. Handful of states restrict employer use | SNIPPET, secondary | [Certiphi](https://certiphi.com/resource-center/compliance-services/sex-offender-registry-information) |
| Individualized assessment | Registry status is not an automatic disqualifier in employment settings | SNIPPET | [GCheck](https://gcheck.com/blog/fcra-sex-offender-registry-check/) |
| Misuse | Every site read warns of prosecution or civil liability for harassment or crime against registrants | FETCHED | NSOPW, Iowa, TX, HI pages above |

**What this means for the design**
- Our use case is patient admission safety, not employment. That is a different legal posture, but **I am not a lawyer and this needs the client legal sign-off** before any production use.
- Results must be framed as "potential match, verify before acting". Never auto-deny admission. The PRD already routes to manual review. Make that mandatory for FLAGGED and INCONCLUSIVE.
- Include a disclaimer string and data freshness in every response.
- Real PII handling stays synthetic in dev.

---

## 6. HIPAA and audit logging

| Topic | Finding | Confidence | Source |
|-------|---------|-----------|--------|
| Audit controls | 45 CFR 164.312(b) is a required specification: mechanisms that record and examine activity in systems with ePHI. It sets no retention period and names no format | SNIPPET | [Syskit](https://www.syskit.com/blog/hipaa-audit-log-retention-requirements), [Aptible](https://www.aptible.com/hipaa/audit-log-retention) |
| 6-year retention | Comes from 164.316(b)(2): documentation kept 6 years. Whether logs count as that documentation is interpretation, not explicit text | SNIPPET | [Syskit](https://www.syskit.com/blog/hipaa-audit-log-retention-requirements) |
| Log review | 164.308(a)(1)(ii)(D) requires regular review of audit logs. Generating logs without reviewing them fails this | SNIPPET | [ISPartners](https://www.ispartnersllc.com/blog/hipaa-audit-log-retention-six-years/) |
| Tamper-evidence | Not mandated by these sources. HIPAA integrity rule 164.312(c) not checked yet | SNIPPET (negative) | |
| Hash chain design | Hash-chained append-only table + read-only verifier + external anchor of chain head. Triggers blocking UPDATE/DELETE/TRUNCATE. Fix canonical field order so verifier and trigger produce same digest | SNIPPET | [AppMaster tamper-evident trails](https://appmaster.io/blog/tamper-evident-audit-trails-postgresql) |
| Limits of chains | Attacker with full DB access can recompute the whole chain. Fix by publishing chain head externally (RFC 3161 timestamp or object-locked storage). Superuser can disable triggers. Concurrent appends can fork the chain, so serialize writes (advisory lock) or chain per tenant | SNIPPET + general knowledge, verify in Postgres docs | same |

**Plan changes:** default retention 6 years (configurable, flagged as interpretation), add an audit review report job, serialize chain appends, optional chain-head anchor export, canonical JSON spec for hashing.

---

## 7. Matching engine research

| Topic | Finding | Source |
|-------|---------|--------|
| Real-world model | OpenSanctions (OFAC-emulating): Jaro-Winkler on whole names and on tokens, best score across all query and candidate aliases, then **penalties when DOB or country diverge**. Name-only methods deprecated as too aggressive. Recommends multi-attribute scoring | [OpenSanctions tuning](https://www.opensanctions.org/docs/api/tuning/), [scoring](https://www.opensanctions.org/docs/api/scoring) |
| Thresholds | No authoritative numeric cutoff found. Tune on own labeled data | search summary |
| Trade-off | Too strict misses real hits, too loose floods reviewers. Nicknames and transliteration are not solved by fuzzy algorithms alone, need variant data | [Flagright](https://www.flagright.com/insight/name-screening) |
| Jaro-Winkler in rapidfuzz | `prefix_weight` default 0.1, valid range 0 to 0.25, optional `processor`, `score_cutoff`. Standalone `jarowinkler` package reports 0.9037 for "Johnathan" vs "Jonathan" | [jarowinkler](https://github.com/rapidfuzz/jarowinkler), [PyPI](https://pypi.org/project/jarowinkler/2.0.0) |
| Nicknames | `nicknames` PyPI package from carltonnorthern: hand-curated, about 1,100 canonical names, one-directional (al -> alexander, not reverse), skewed toward traditionally African American names, old names from a genealogy source. Bigger option: Princeton/LDC Intelius collection, 331,237 mappings, licensed, not free | [carltonnorthern/nicknames](https://github.com/carltonnorthern/nicknames), [PyPI](https://pypi.org/project/nicknames), [Princeton DSS](https://dss.princeton.edu/catalog/resource6188) |
| Caution on identity | NSOPW-based tools note a name search returns aliases and spelling variants and a row is not proof of the same person | [Apify actor](https://apify.com/dromb/nsopw-offender-search) |

**Plan changes:** add whole-name + token JW comparison, add explicit DOB-divergence penalty framing, build both-direction nickname index from `nicknames`, record threshold calibration as an experiment, keep Levenshtein as a switchable baseline.

---

## 8. Resilience libraries (Python)

| Library | Notes | Source |
|---------|-------|--------|
| aiobreaker | Fork of pybreaker with native asyncio. Failure threshold, reset timeout, excluded exceptions, optional Redis | [PyPI](https://pypi.org/project/aiobreaker) |
| purgatory | Async and sync, memory or Redis state, event hooks, typed. Docs site marked obsolete | [GitHub](https://github.com/mardiros/purgatory) |
| pybreaker | Upstream sync lib, async support weak | [PyPI](https://pypi.org/project/aiobreaker) |
| interlock-cb | New (2026), failure-rate over sliding window instead of consecutive failures | search summary |

No head-to-head comparison or maturity data found. **Plan stays:** own small breaker (sliding-window failure rate), designed behind a `Breaker` interface so aiobreaker or purgatory can replace it. Check release activity before swapping.

---

## 9. Standards worth knowing

- **SORNA Exchange Portal** (SMART Office): lets jurisdictions share offender relocation info, has web services for jurisdictions. Not a public search interface for us. [OJP](https://ojp.gov/index%2ephp/library/publications/sorna-exchange-portal)
- **NIEM SORT Entry IEPD**: request/result exchange with identity, demographics, convictions, locations, vehicles. Useful as a field checklist for our data dictionary, not as something we integrate. [NIEM](https://niem.gov/node/459)

---

## 10. Gaps still open

1. 50-state matrix: only about 9 jurisdictions touched here. R2 must cover the rest with primary sources.
2. Iowa: API documented, but 50 requests per hour is a hard design constraint. the client's existing integration still useful for conventions.
3. FL, TN, IA pages failed to load in this session. Re-verify manually in a browser.
4. NSOPW organizational access: check whether SMART Office offers approved bulk or API access for healthcare organizations (ask via Contact Us).
5. Vendor shortlist and their FCRA / permitted-use terms. Official Offendersearch docs not found.
6. Whether the client's admission use falls under any state-specific use restriction. Needs legal.
7. HIPAA integrity rule 164.312(c) and retention policy of the client.
8. Tribal registries (151 on NSOPW): out of scope unless you say otherwise.

---

## 11. Source index

NSOPW: [Conditions](https://nsopw.gov/en/conditions), [Organizations conditions](https://organizations.nsopw.gov/Conditions), [Suspended notice](https://www.nsopw.gov/suspended), [34 USC 20920](https://www.govinfo.gov/link/uscode/34/20920), [SMART FY25 grant listing](https://highergov.com/grant-opportunity/smart-fy25-maintenance-and-operation-of-the-dru-sjodin-national-sex-offender-pub-358083)

States: [Iowa API guide](https://www.iowasexoffender.gov/feed/info), [Iowa FAQ](https://www.iowasexoffender.gov/faq/content), [Iowa conditions](https://organizations.nsopw.gov/Conditions/JurisdictionConditions/IA), [Iowa data.gov entry](https://catalog.data.gov/dataset/iowa-sex-offender-registry), [Texas export](https://sor.dps.texas.gov/Home/Export), [Florida data file](https://www.fdle.state.fl.us/SOPS/public_data_file), [Hawaii bulk](https://hcjdc.ehawaii.gov/bulkcor/public/about.html), [Arkansas download](https://portal.arkansas.gov/service/ar-sex-offender-registry-data-download/), [Kansas KBI](https://www.kansas.gov/kbi/ro.shtml), [Tennessee MapServer](https://tnmap.tn.gov/ArcGIS/rest/services/PUBLIC_SAFETY/TBI_SEX_OFFENDER_REGISTRY/MapServer/info/iteminfo?f=pjson), [DC dataset](https://catalog.data.gov/dataset/sex-offender-registry), [California (UC Davis)](https://police.ucdavis.edu/professional-standards/megans-law)

Vendors: [IDScan](https://docs.idscan.net/screening/sex-offender/sex-offender.html), [Certn](https://centric-api-docs.certn.co/reference/check-US_NATIONAL_SEX_OFFENDER_REGISTRY_1.md), [Offendersearch listing](https://mcpservers.org/servers/offendersearch/mcp), [Zyla](https://www.zylalabs.com/api-marketplace/data/offender+registry+usa+api/2117), [OffenderWatch](https://offenderwatch.com/national-sex-offender-registry)

Legal: [Maryland HB 1072](https://mgaleg.maryland.gov/2014RS/fnotes/bil_0002/hb1072.pdf), [PA Megan's Law nursing home bill](https://pahouse.com/InTheNews/NewsRelease/?id=122707), [Littler on California](https://www.littler.com/publication-press/publication/californias-new-megans-law-website-employers-are-cautioned-not-make), [GCheck FCRA](https://gcheck.com/blog/fcra-sex-offender-registry-check/)

HIPAA and logging: [Syskit](https://www.syskit.com/blog/hipaa-audit-log-retention-requirements), [Aptible](https://www.aptible.com/hipaa/audit-log-retention), [AppMaster hash chain](https://appmaster.io/blog/tamper-evident-audit-trails-postgresql)

Matching and libs: [OpenSanctions tuning](https://www.opensanctions.org/docs/api/tuning/), [jarowinkler](https://github.com/rapidfuzz/jarowinkler), [nicknames](https://github.com/carltonnorthern/nicknames), [aiobreaker](https://pypi.org/project/aiobreaker), [purgatory](https://github.com/mardiros/purgatory), [NIEM SORT](https://niem.gov/node/459)

---

## 12. Round 2: which real sources are easy now (2026-10-15)

Tested from this machine with direct requests. Field names and shapes only, no personal data kept.

| Source | Result | Detail |
|--------|--------|--------|
| **DC** ArcGIS FeatureServer layer 20 | **Works, no account** | `https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/MPD/FeatureServer/20`, CC BY 4.0, source CSOSA, 1,053 records, about 1s per query, max 1000 per page, updated daily. Fields: LASTNAME, FIRSTNAME (no middle name), ALIASES (text, entries joined by ` / `, present on 250 of 300 sampled), BIRTHDATE (epoch ms at 04:00 or 05:00 UTC, so the UTC date is the right date), ZIPCODE, BLOCKNAME (block-level address), MAXCLASSIFICATION, TYPE. No city or state field. A search snippet says only Class A and B offenders are included, not verified. [data.gov entry](https://catalog.data.gov/dataset/sex-offender-registry) |
| **Tennessee** TBI MapServer | Unreachable from here | Connection hangs, no TCP connect. A fetch from another network also reset. Listed as nightly updated, 2,000 max per query. Field list unknown. [service](https://tnmap.tn.gov/arcgis/rest/services/PUBLIC_SAFETY/TBI_SEX_OFFENDER_REGISTRY/MapServer) |
| **Missouri** Highway Patrol NSOR MapServer | Unreachable from here | Same hang. Newly found lead. [service](https://www.mshp.dps.mo.gov/arcgis/rest/services/NSOR/MapServer) |
| **Florida** FDLE data file | Unreachable from here | Both FDLE pages hang. Needs a browser check on your side. |
| **Texas** DPS export | **Done 2026-10-15**, real file imported, see section 3.2 | [export page](https://sor.dps.texas.gov/Home/Export) |

### Commercial APIs for the other states (all need a key, none tested)

| Vendor | Search | Coverage and freshness | Cost | Fit notes |
|--------|--------|------------------------|------|-----------|
| [Offenders.io](https://offenders.io/) | `GET api.offenders.io/sexoffender?firstName&lastName&zipcode&key`, optional `fuzzy=true` (+$0.02) | Claims 184 jurisdictions, synced continuously, optional live lookups | First 50 requests per month free, then pay as you go | **HIPAA/PHI only for Enterprise customers under a BAA. Standard accounts must not send PHI.** Not for FCRA decisions |
| [Nannostomus](https://www.nannostomus.com/api/) | name, DOB, city, state, zip | Claims 50 states, territories, tribes, 820K records. **Refreshed monthly** | 100 free per month, then $0.15 down to $0.01 per request | Not FCRA. Lists healthcare uses but no healthcare terms. No match confidence |
| [CrimeoMeter](https://www.crimeometer.com/sex-offenders-api) | name or zip only | "Across the US", frequency unknown | Not listed, key by contact form | No DOB search |
| [RegistryRadar](https://registryradar.app/api) | coordinates or bounding box only | 14 of 56 jurisdictions live | 1,000 free per month | **No name search, so unsuitable.** Not FCRA, empty is not all-clear |

Key point for production: patient name plus date of birth sent by a healthcare provider is PHI. Any vendor needs a signed BAA before real patients. Fake data only until then.

### DC live test results (2026-10-15)

- 1,053 rows are 855 distinct people (`SEXOFFENDERCODE`), with TYPE HOME 794, WORK 240, SCHOOL 19, so rows must be merged. Classification A 598 rows, B 455 rows. All rows have a zip and a first name.
- Layer returns 1000 rows per page and signals more with `exceededTransferLimit`, paging with `resultOffset` works. A wide query returned 752 people over 2 pages in 0.7s.
- Alias matters: for SMITH, 12 of 26 people matched only through an alias. A surname-only query would have missed them.
- Layer metadata shows data as of 2026-10-14. Query time 0.3 to 1.2s.

### Texas live test results (2026-10-15)

- Free account created on https://sor.dps.texas.gov/Home/Create (personal account, placeholder contact details, user's email). The download page then asks for contact details, which DPS says are stored and open to records requests. Download is `DownloadFile?date=20261014&exportcode=DATA-CURRENT`, 37.9 MB zip, regenerated twice a week.
- The zip is a set of tab-delimited database tables plus SQL import scripts, not a flat file. It also carries a nested 2024 zip of online identifiers (not used). Layout from its own Layout.pdf and DisplayRecord.sql: INDV (IND_IDN to DPS_NBR), PERSON, NAME (TYP_COD B is the primary name, S and H are aliases, FNA_TXT holds first, middle and suffix), BRTHDATE (B primary, S alternate), INDV_SOR (RSK_COD 1 High, 2 Moderate, 3 Low, C civil commitment, N not reported, U unknown), Address (one row per person, about 61 percent have street, city and zip), AddressEvent (PDV_COD VAL means Texas resident, others are absconded, incarcerated, out of state, deceased and so on).
- 108,324 people, all with a primary birth date, 90,725 with at least one alternate name, 21,327 alternate birth dates (not used yet). 26 percent of people share an exact first and last name with another record.
- The importer is `src/sor/importers/tx_dps.py` (reads zip members by exact name, never extracts). Checks take 12 to 45 ms from memory.
- Matching on real data: 47 people come back for a made-up JOHN SMITH (22 with that exact name, 28 through an alias, all with other birth dates), so common names end INCONCLUSIVE. Not a bug, but it means a common-named patient always needs a human unless the rule for same name with a far-off birth date changes.
- FDLE (Florida): every FDLE host timed out from this machine (home page too) while google.com loaded, so Florida was not attempted.
