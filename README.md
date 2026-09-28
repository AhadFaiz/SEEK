
# SEEK — Saudi Etimad Extraction & Knowledgebase

An automated ELT data pipeline on Azure that collects Saudi government tenders from Etimad every day, stores them raw, and transforms them into a bilingual, sector-classified historical archive.

Etimad removes a tender once it closes, publishes in Arabic only, and offers no business-sector grouping. SEEK keeps every tender it sees, adds English next to every Arabic text field, classifies each tender into one of 16 sectors, and maintains one up-to-date row per tender.

**Users:** anyone who bids on Etimad tenders — contractors and suppliers in every sector — and market analysts.

Capstone project — Data Engineering Program, Saudi Digital Academy × WeCloudData (September 2026).
Team: Ahad Faiz Alotaibi · Aseel Aldawood · Atheer AL Abdullah

## Architecture

| Step | Tool | What it does | Schedule |
|---|---|---|---|
| E — Extract | Azure Function `seek-pipeline-function` (Python) | Calls Etimad's listing endpoint and per-tender detail endpoints | Daily, 10:00 AM Riyadh (07:00 UTC) |
| L — Load | Azure Data Lake Storage Gen2 (`ahadfaiz1`) | Stores the raw JSON unchanged in `seek-data-landing` | With every extraction |
| T — Transform | Azure Data Factory, `SEEK_Transform_Pipeline` | Classifies, translates, validates and archives | Daily, 11:00 AM Riyadh (trigger `daily-seek-transform`) |

## Storage containers

| Container | Layer | Written by | Contents |
|---|---|---|---|
| `seek-data-landing` | Raw | Python extractor | `data/raw/etimad_all_tenders_<date>.json` (one per day) and `data/raw/tender_details_by_id.json` (cumulative) |
| `seek-adf-internal` | Working | Azure Data Factory | `translation_cache.json` (JSON Lines), `sector_keywords.csv`, `staging/` |
| `seek-public` | Final | Azure Data Factory | `tender_copy.csv` — the historical archive |

## Transformation steps (Azure Data Factory)

1. **DeriveAndClassify** — derives `source_entity` (parent entity without branch detail) and assigns a sector by joining `sector_keywords.csv`. Tenders that match no keyword are labelled `Other - needs review: <source_entity>`, never dropped.
2. **SplitCachedVsNew** — separates Arabic texts already in the translation cache from new ones.
3. **Lookup_NeedsTranslation → ForEach_TranslateNewTexts** — sends each new text to Azure AI Translator and saves the result.
4. **MergeAndUpdateCache** — adds new translations to the cache; keeps one row per Arabic text.
5. **ApplyTranslations** — adds an English `_en` column next to each Arabic text column. The Arabic original is kept as the official text.
6. **RenameAndValidate** — renames columns to snake_case, cleans whitespace and line breaks, splits valid and rejected rows.
7. **UpsertArchive** — merges the day's rows with the existing archive, keeps one row per `reference_number`, records `first_seen_date` and `last_updated`, and writes `tender_copy.csv`.

Every step runs only if the previous one succeeded.

## Why ELT

The first version ran every step in Python inside the Azure Function (ETL). On 21 September 2026, following our supervisor's advice, all transformation was moved into Azure Data Factory. Python now only extracts and loads. Keeping the raw layer let us rebuild the archive from raw files while fixing defects, without re-scraping Etimad. The original Python modules in `src/` remain as the reference specification of the transformation logic.

## Repository structure

SEEK/ ├── azure_function/ # Azure Function (extract + load): function_app.py, host.json, requirements.txt ├── src/ # Python modules │ ├── extract.py # extraction logic used by the Azure Function │ ├── config.py # non-secret settings, paths and constants │ └── clean.py, profile.py, schema.py, transform.py, notify.py │ # original Python transformation (reference specification for ADF) ├── notebooks/ # development notebooks: extract, profile/clean, validate, join/transform, upload ├── scripts/ │ ├── check_output.py # verifies the archive and reconciles it with raw files │ └── convert_cache.py # converts the local translation cache to JSON Lines for ADF ├── seek_dbt/ # star-schema prototype (dbt + DuckDB): fact_tender + 4 dimensions ├── adf/ # exported Azure Data Factory ARM template ├── data/ # local data folders (contents are gitignored) ├── tests/ # unit tests (pytest) ├── main.py # local entry point for extraction ├── config.yaml └── requirements.txt
## Setup

git clone https://github.com/AhadFaiz/SEEK.git cd SEEK python3 -m venv .venv source .venv/bin/activate pip install -r requirements.txt
Create a `.env` file in the project root (it is gitignored — never commit it):

ADLS_ACCOUNT_NAME=<storage account name> ADLS_SAS_TOKEN=<SAS token for the storage account> AZURE_TRANSLATOR_KEY=<Azure AI Translator key> # only needed by the original Python transformation modules
The Etimad listing pages need no login or key.

## How to run

### 1. Extraction (local)

python main.py
Writes the day's raw listing file and the cumulative tender-details file to `data/raw/`. In production the same code runs in the Azure Function on its daily timer and uploads to `seek-data-landing`; the Function reads `ADLS_ACCOUNT_NAME` and `ADLS_SAS_TOKEN` from its Application settings.

### 2. Transformation (Azure Data Factory)

The transformation runs only in Azure Data Factory, not locally. The pipeline definition is in `adf/`. It runs daily on the `daily-seek-transform` schedule trigger, or manually from ADF Studio (`SEEK_Transform_Pipeline` → Add trigger → Trigger now). Run history is in ADF Studio → Monitor → Pipeline runs.

### 3. Verify the output

Download `tender_copy.csv` from `seek-public` (do not open or save it with Excel — it can garble the UTF-8 Arabic text), then run:

python scripts/check_output.py --archive path/to/tender_copy.csv --raw data/raw
It checks CSV structure, key uniqueness, helper columns and line breaks, English coverage of every `_en` column, the sector distribution, and — with `--raw` — reconciles the archive with the raw daily files. It ends with `ALL CHECKS PASSED` or the number of checks that need attention.

### 4. Unit tests

pytest
12 offline tests cover the extraction step.

### 5. Star schema prototype (optional)

cd seek_dbt dbt run
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
