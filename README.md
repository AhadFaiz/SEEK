
Requires `dbt-duckdb` and a DuckDB profile in `~/.dbt/profiles.yml`. Builds `stg_tenders_archive`, `dim_agency`, `dim_sector`, `dim_tender_type`, `dim_date` and `fact_tender`. This is a design prototype for future reporting, not part of the production pipeline.

## Source definition

**Source:** Etimad — Saudi government e-procurement portal (منصة اعتماد), public visitor pages.

**SRC-1 · Listing endpoint** `https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync`

Query parameters: `PageSize` (24 per page), `PublishDateId` (5, the public listing default), `pageNumber`.

**SRC-2 · Detail endpoints** (under `tenders.etimad.sa/Tender/`), one call each per tender, fetched once and stored in `tender_details_by_id.json`:
`GetTenderDatesViewComponenet` (dates), `GetRelationsDetailsViewComponenet` (classification and place of execution), `GetAwardingResultsForVisitorViewComponenet` (awarding results), `GetLocalContentDetailsViewComponenet` (local content). These return HTML fragments, parsed with BeautifulSoup.

**Authentication:** none. The listing endpoint is the public, unauthenticated JSON endpoint used by the portal's own front-end.

**Rate limits:** not officially documented. The endpoint returned HTTP 429 during testing when several runs were started at once. The production extractor retries each page up to 6 times, waiting 20 seconds × the attempt number.

**Licence / terms of use:** the listing pages are openly viewable without authentication, and the data is public-interest government procurement information. A formal developer API portal exists at `apiportal.etimad.sa`; this project uses the public endpoint above because the formal API's access terms were unresolved at the time of writing. This is documented as a known limitation, not treated as a licensed integration.

**Scope:** SEEK reads 20 pages of 24 tenders — the first 480 listed tenders per day. Etimad listed 7,109 tenders on 24 September 2026. Scaling to the full listing is planned.

## Data dictionary — `tender_copy.csv`

One row per tender, 48 columns.

| Column | Meaning | Source |
|---|---|---|
| `reference_number` | Public tender reference — unique key | Source, renamed |
| `tender_id` | Etimad's internal tender ID | Source, renamed |
| `tender_name` / `tender_name_en` | Tender title, original and English | Source / translation cache |
| `agency_name` / `agency_name_en` | Issuing agency, original and English | Source / translation cache |
| `source_entity` / `source_entity_en` | Parent entity without branch detail | Derived / translation cache |
| `sector` | One of 16 business sectors, or `Other - needs review: <source_entity>` | Keyword classification |
| `branch_name` / `branch_name_en` | Branch office | Source / translation cache |
| `tender_type_name` / `tender_type_name_en` | Competition type | Source / translation cache |
| `tender_activity_name` / `tender_activity_name_en` | Activity / category | Source / translation cache |
| `tender_status_name` / `tender_status_name_en` | Tender status | Source / translation cache |
| `tender_number` | Agency's own tender number (not translated) | Source |
| `last_offer_presentation_date` | Bid submission deadline | Source |
| `offers_opening_date` | Bid opening date — empty for Direct Purchase tenders | Source |
| `buying_cost`, `financial_fees`, `invitation_cost` | Tender costs (SAR) | Source, checked as numeric |
| `first_seen_date` | Time the tender first appeared in our snapshots | Derived |
| `last_updated` | Time of the latest snapshot containing the tender | Derived |

The remaining source columns (Hijri dates, remaining-time counters, platform flags and IDs) are passed through with snake_case names. `created_at` is always `0001-01-01` at the source and is not used.

## Results

- Scheduled extraction successful every day since 21 September 2026. Run time dropped from 107 minutes (22 Sept, first full load) to about 5 minutes (24–25 Sept).
- First scheduled transformation run (24 September, 11:00 AM): 40 of 40 activities succeeded in 17.5 minutes, translating 16 new titles automatically. The 25 September run succeeded in 33.6 minutes.
- Archive (as of 23 September): 1,411 tenders from snapshots of 10–23 September, 0 duplicate or broken rows, 1,411 raw tenders = 1,411 archive tenders.
- Translation cache: 3,288 distinct texts, one row each. The first real run needed only 351 new translations out of ~1,300, thanks to the cache.
- As of the 26 September check, English coverage is 100% for every translated column except `source_entity_en` (99.6%).
- 12 offline unit tests (pytest) cover the extraction step and all pass. Output is also verified end-to-end with `scripts/check_output.py`.
- Re-running the pipeline on the same data produces the same archive.

## Known limitations

- SEEK reads the first 480 listed tenders per day, out of 7,109 listed on Etimad (24 September 2026). Scaling to the full listing is planned.
- 12% of tenders (170 of 1,411, as of 23 September) match no sector keyword and are labelled `Other - needs review`. They are kept, never dropped.
- Tender detail sections are stored raw and are not yet part of the archive; details are fetched once per tender, so awarding results published later are not captured.
- Machine translation can make mistakes, so the Arabic text is always kept as the official version.
- The ADF schedule is time-based: if extraction were late, the transformation would process the previous day's files.

## Security & Operations

- No secrets in code: they live in `.env` locally (gitignored) and in Azure Function Application settings.
- Azure Data Factory reaches storage through Linked Services. The Translator key is currently an ADF pipeline parameter; moving it to Azure Key Vault is planned.
- A Translator key committed early in development was removed by rebuilding the repository history on a clean branch.
- Azure Monitor alert rule `SEEK extraction failed` emails the team if extraction fails.
