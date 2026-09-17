# Club Gas Prices — Dashboard

The Quarto site behind
<https://dashboard.thecoatlessprofessor.com/club-gas-prices/>: posted fuel prices at
warehouse-club gas stations, currently every Costco station in the United States,
Canada, Mexico, the United Kingdom, Australia, Japan and Taiwan.

This repository holds the site only. The prices are collected and published by
[coatless-data/club-gas-prices](https://github.com/coatless-data/club-gas-prices),
and that is where the dataset, its schema and its release history live.

> [!IMPORTANT]
> Unofficial. Prices are collected from public websites and may differ from the price at
> the pump.
>
> Not affiliated with, endorsed by, or connected to Costco Wholesale Corporation.
>
> Not affiliated with, endorsed by, or connected to Sam's West, Inc., Sam's Club, or
> Walmart Inc.

## The pages

| Page | What it shows |
|---|---|
| Map | Every station with coordinates, colored by rank within its country or on an absolute USD scale. Stations group while the map shows a continent and separate as it is zoomed in. The popup gives the published price, the local price, the USD price, the exchange rate used and the age of the reading. |
| Compare | One row per country for the selected grade: median, p25–p75 and the number of stations, in USD or in local currency, with a breakdown by region. |
| Trends | Daily medians over time for every country, and regional series for one country at a time. Each chain is drawn as its own line. |
| Changes | How often a posted price moved, as a heatmap by chain and US state, with a drill-down to one state's stations. |
| Station | One station's price history per grade, its last thirty price changes, directions, its warehouse page where the source publishes one, and the five nearest active stations. Deep-linkable as `station.html?station=<station_key>`. |
| About | Sources and request URLs, the capture schedule, the grade table, units, the exchange-rate method, known limits and attributions. |

Three controls apply to every page: **Grade** (`regular`, `premium`, `diesel`),
**Currency** (`USD` or `Local`) and **Volume** (`per US gallon` or `per litre`). They
live in the query string, so a link carries what the sender was looking at. USD values
always use the exchange rate stored with each row, so a chart of the past shows the USD
prices of the past, not today's conversion.

Where a country has more than one chain, each is a separate series and they are never
pooled into one number.

## How it gets its data

The collector publishes five files into its `current` release, under a `site-` prefix,
with a size and sha256 for each in `manifest.json`:

| Asset | Becomes | What it holds |
|---|---|---|
| `site-meta.json` | `data/meta.json` | The capture id, per-country status, the grade table, the notices, the basemap and the release links |
| `site-latest.json` | `data/latest.json` | One record per station with coordinates: its current price per grade |
| `site-stations.json` | `data/stations.json` | Every station ever seen, active or closed, with its address and identifiers |
| `site-summary-daily.parquet` | `data/summary_daily.parquet` | Daily medians and quartiles by country, chain and region |
| `site-history.parquet` | `data/history.parquet` | Daily price per station and grade, with day-over-day and intraday change flags |

Render downloads them, checks them against the manifest, drops the prefix and renders.
Nothing in this repository computes a price, so a rendered page can only be as fresh as
the release it was built from — `meta.json` carries the capture id and the page says so.

A `GITHUB_TOKEN` cannot start a workflow in another repository, so Render polls rather
than being pushed to, and skips a scheduled run whose capture is already deployed.
Reading a public repository's releases needs no credential, which is the reason to poll
here rather than hold a token issued by the other repository.

## Running it locally

```bash
uv sync
uv run python tests/fixtures/site_sample.py site/data   # or fetch the real thing, below
quarto preview site
```

`site_sample.py` writes a small, real set of prices captured on 2026-09-15 with the
Frankfurter reference rates of 2026-09-14 — enough to render every page and to smoke
test them. To work against the live data instead:

```bash
gh release download current -R coatless-data/club-gas-prices \
  -D site/data --pattern 'site-*' --pattern manifest.json
python3 .github/verify-site-data.py site/data
```

Tests and lint:

```bash
uv run pytest
uv run ruff check && uv run ruff format --check
```

The smoke test drives the built site with Playwright and is not part of `pytest`:

```bash
quarto render site && cp -R site/data _site/data
uv sync --group smoke && uv run playwright install chromium
uv run python tests/smoke/smoke_site.py _site
```

## Repository layout

| Path | What it is |
|---|---|
| `site/` | The Quarto website. `_shared.qmd` holds every helper and cell the pages share; `_controls.qmd` the control bar; `_footer.qmd` the notice. One `.qmd` per page. |
| `site/custom.scss`, `site/dark.scss` | Theme, control styling and the map chrome that follows Quarto's light/dark toggle. |
| `.github/workflows/render.yml` | Fetch the release, verify, render, smoke test, deploy to Pages. |
| `.github/workflows/test.yml` | Lint, `pytest`, and a full render and smoke test against the sample data on every push and pull request. |
| `.github/verify-site-data.py` | Checks a download against `manifest.json` and unprefixes it. |
| `tests/fixtures/site_sample.py` | The local sample data. |
| `tests/smoke/` | The Playwright script and the static server it runs against. |

## The seam

Two things in this repository restate something the data repository owns, because the
code that produces them lives there:

- `.github/verify-site-data.py` names the five assets — `club_gas.sitedata.SITE_ASSETS`.
- `tests/test_site_sample.py` names the parquet columns the pages query —
  `club_gas.sitedata.HISTORY_COLUMNS` and `SUMMARY_COLUMNS`.

Both are commented as such on both sides. A column added there without a change here
renders a page against a shape it never receives, and `test_site_sample.py` is what
fails first.

## License

MIT. See [LICENSE](LICENSE). The prices belong to the retailers; see the notice above.
