# Prince George rack rates

This repo keeps a daily history of Petro-Canada **Daily** rack prices for **Prince George, BC**. Prices are in cents per litre, and each row is keyed by the page's effective date.

- **Dataset:** [`data/pg_rack_rates.csv`](data/pg_rack_rates.csv)
- **Raw URL:** `https://raw.githubusercontent.com/zyujia-crypto/PGR/main/data/pg_rack_rates.csv`
- **Google Sheets:** `=IMPORTDATA("https://raw.githubusercontent.com/zyujia-crypto/PGR/main/data/pg_rack_rates.csv")`
- **Excel:** Data → From Web → paste the raw URL → Load. In Query Properties, turn on "Refresh data when opening the file".

A blank price means the source was blank on that date.

Updates come from `.github/workflows/update.yml`, which runs twice each morning after Petro-Canada's approximate 3 a.m. Eastern posting time. Operations and troubleshooting are covered in [the runbook](.claude/skills/prince-george-rack-rates/references/cloud-workflow.md).

To run it locally:

```sh
pip install -r requirements.txt
python -m pytest -q
PYTHONPATH=src python -m pgrack capture   # fetch and save page HTML
PYTHONPATH=src python -m pgrack run       # update data/pg_rack_rates.csv
```
