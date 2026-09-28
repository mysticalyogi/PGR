# Cloud workflow runbook

## Pieces

| Path | Role |
|---|---|
| `data/pg_rack_rates.csv` | Canonical dataset. Only the workflow writes it. |
| `data/pg_rack_rates.prev.csv` | The dataset as it was before the last write. Use it for quick recovery. Git history is the full record. |
| `data/run_log.csv` | Append and revise events. Skips go only to the Actions job summary. |
| `src/pgrack/fetch.py` | Fetch strategies, tried in this order: `direct` (requests), `browser` (Playwright Chromium), then `service` (hosted scraper, used only when `SCRAPER_API_KEY` is set). |
| `src/pgrack/parse.py` | Parser. It finds the Daily panel, maps columns by heading, and requires exactly one Prince George row. |
| `src/pgrack/store.py` | Upserts one row per `effective_date`. Writes are atomic: temp file, validate, back up the previous file, then `os.replace`. |
| `.github/workflows/update.yml` | Runs on schedule and on manual dispatch (`mode=run` or `mode=capture`). |

## Schedule

The workflow runs at 08:17 and 10:47 UTC. That is 04:17 and 06:47 EDT, or 03:17 and 05:47 EST, which keeps both runs after Petro-Canada's posting time of about 3:00 a.m. Eastern. GitHub can start scheduled runs several minutes to an hour late.

- **First attempt:** `continue-on-error`. A failure here is quiet, because the second attempt may recover.
- **Second attempt, or any manual run:** a failure opens or updates the issue labelled `tracker-failure`. The next successful run closes that issue, and the close is the repair notice.
- **Revisions:** each revision is recorded as a comment on the issue labelled `rate-revision`.
- **Unchanged runs:** these send no notification.

GitHub disables scheduled workflows after 60 days with no repository activity. If data commits stop for that long, re-enable the workflow under Actions → Update rack rates.

## Secrets and variables

These are set under Settings → Secrets and variables → Actions:

- `SCRAPER_API_KEY` (secret, optional): the API key for the hosted service. It must never be committed.
- `SCRAPER_PROVIDER` (variable, optional): `scrapingbee` (default) or `zenrows`. The endpoint and parameters for each provider are in `fetch.py` under `_SERVICES`.

## Diagnosing a failure

The `category` in the failure issue tells you where to look.

- **`blocked`:** every fetch method returned 403, 429, or a challenge page. Datacenter IPs are the usual cause.
  1. Add or check `SCRAPER_API_KEY`.
  2. Confirm the service still has credits.
  3. Do not add retry loops, spoof browser fingerprints, or try to solve CAPTCHAs.
- **`fetch`:** there was a network error, or the page loaded without the Daily table markers. Run `mode=capture` and read the job log. The log prints the HTML around "Daily" and "Prince George", and the full HTML is uploaded as the `rack-page-capture` artifact.
- **`parse`:** the page structure changed or is ambiguous. The fetched page is uploaded as the `failed-page-<run_id>` artifact.
  1. Save it as a new fixture under `tests/fixtures/`.
  2. Decide whether the change is a real renaming, which needs your confirmation before the headings are changed, or a layout change.
  3. Update `parse.py` and add a test.
  4. Never loosen the heading or location matching just to get a green run.
- **`store`:** the CSV on `main` failed validation, for example because of a duplicate date, a bad header, or rows out of order. Repair it from `pg_rack_rates.prev.csv` or from git history, then re-run.

## Manual operations

- **Re-run now:** Actions → Update rack rates → Run workflow (`mode=run`).
- **Capture the page:** use `mode=capture`. It can be restricted with `methods`, for example `direct` or `service`.
- **Dry-run a saved page against the dataset:** run `PYTHONPATH=src python -m pgrack parse-file page.html --upsert-into data/pg_rack_rates.csv`. It works on a scratch copy and prints the result, so the real file is never touched.

## Integration

The raw CSV URL is `https://raw.githubusercontent.com/zyujia-crypto/PGR/main/data/pg_rack_rates.csv`. It only works without credentials if the repo is public.

- **Google Sheets:** `=IMPORTDATA("<raw url>")`. Sheets refreshes this on its own schedule, roughly hourly.
- **Excel:** Data → From Web → paste the raw URL → Load. Then open Query Properties and turn on "Refresh data when opening the file".
