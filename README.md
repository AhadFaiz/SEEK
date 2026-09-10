# SEEK — Saudi Etimad Extraction & Knowledgebase

## Setup


## How to run


## Source Definition

**Source name:** Etimad — Saudi Government Tenders Portal (تنافسية / منصة اعتماد)

**Endpoint URL:**
https://tenders.etimad.sa/Tender/AllSupplierTendersForVisitorAsync


Query parameters:
- `PageSize` — number of tenders returned per request (we use 100)
- `PublishDateId` — publish-date filter used by the portal's own UI (we use 5, the default applied on the public listing page)
- `pageNumber` — 1-indexed page number for pagination

**Authentication method:** None. This is a public, unauthenticated JSON endpoint used internally by the portal's own front-end to render the public tender listing page (discovered via browser DevTools → Network tab). No API key or login is required for read access to public tender listings.

**Rate limits:** Not officially documented. In practice, the endpoint returned `HTTP 429 Too Many Requests` after rapid successive calls during testing. Mitigated in `01_extract.ipynb` with:
- A 3-second delay between page requests
- Automatic retry with exponential backoff (5s, 10s, 15s, 20s) on `429` responses, up to 4 attempts per page

**Licence / terms of use:** Etimad's public listing pages are openly viewable without authentication, and the underlying data (tender name, agency, deadlines, reference numbers) is public-interest government procurement information. A formal developer API/licence portal exists at `apiportal.etimad.sa`; this project uses the endpoint above (discovered via the public web UI, not the formal API portal) since the formal API's access requirements were unresolved as of this writing. This should be flagged to the instructor as a known limitation, not treated as a fully licensed integration.

**Data returned per record (key fields):** `tenderId`, `referenceNumber`, `tenderName`, `tenderNumber`, `agencyName`, `branchName`, `tenderActivityName`, `submitionDate`, `remainingDays`, and related status/type fields. Full raw record is saved unmodified to `data/raw/`.

## Data dictionary