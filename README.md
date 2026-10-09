# bank-rates

ดึงประกาศอัตราดอกเบี้ยเงินฝาก (PDF) ของธนาคารไทย แล้วแปลงเป็นตารางเดียว `data/rates.csv`
Scripts that fetch Thai bank deposit-rate announcements (PDF) and turn them into one tidy table.

ตอนนี้มี **KBank** (กสิกรไทย). ต่อไปเพิ่ม SCB, BBL, KTB, KKP ได้ / Banks: KBank now; SCB, BBL, KTB, KKP later.

## Layout

| path | what |
|---|---|
| `banks/kbank.py` | scraper + parser for KBank (`python -m banks.kbank`) |
| `banks/common.py` | shared helpers: Thai text normalising, CSV upsert, headless-browser fetch |
| `rules/kbank.yaml` | URLs, link selector/regex, cookie button, PDF column positions, product-matching rules. **Site or PDF layout changed? edit this first.** |
| `raw/kbank/YYYY-MM-DD-th.pdf` | original PDFs, named by effective date (วันที่มีผล) |
| `data/rates.csv` | all banks, long format |
| `tests/` | checks parsed values against known rates |

## Run

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium   # or: install chromium

python -m banks.kbank                      # fetch latest PDF, parse, update data/rates.csv
python -m banks.kbank --pdf ~/Downloads/09052026-deposit-rates-th.pdf   # fallback: local PDF
python -m banks.kbank --reparse            # re-parse everything in raw/kbank/
python -m banks.kbank --pdf FILE --dry-run # print rows only
python -m pytest -q
```

Re-running is safe: rows for the same bank + effective date are replaced, not duplicated.

### Fetching notes (KBank)
- Plain `curl` / `requests` gets **Access Denied** (Akamai). The script uses Playwright with the
  full Chromium build in new-headless mode (`channel="chromium"`); the slim `chromium-headless-shell`
  also gets 403. The PDF is downloaded with `fetch()` inside the page so it shares the session.
- Cookie banner: click the green **ยอมรับคุกกี้ทั้งหมด** (accept all).
- If the site starts blocking headless browsers, download the PDF by hand and use `--pdf`.

## `data/rates.csv`

`bank, effective_date, product, term, amount_band, customer_type, rate_pct, source_pdf`

- `rate_pct` — % per year (ร้อยละต่อปี)
- `term` — `3M`, `24M`, `7-14D` (fixed term), `>=30D` (min holding days), empty for savings/current
- `amount_band` — `<1M`, `1M-<50M`, `>=100M`, `<=500k`, `>500k`, `0.1M-3M` (ไม่เกิน = inclusive),
  `500-25000 THB/month` (ทวีทรัพย์ monthly instalment), `all`
- `customer_type` — `individual` (บุคคลธรรมดา), `juristic_1`, `juristic_2`, `hospital_school_gov`,
  `financial_institution`, `fund`, `special_juristic_1`, `special_juristic_2`, `non_resident`
- KBank `product` ids: `current`, `k-ecurrent`, `savings`, `savings-collateral-ref`, `k-esavings`,
  `k-esavings-make`, `k-epocket`, `line-bk`, `line-bk-special-savings`, `basic-banking`,
  `savings-special-juristic`, `savings-special-1..6`, `k-green-savings`, `fixed-dated-special-juristic`,
  `fixed`, `family-flex`, `super-senior`, `taweesap`

### How parsing works / known gaps
- Uses `pdfplumber` word positions: each rate is mapped to a customer-type column by its x position
  (`rules/kbank.yaml: pdf.columns`). All customer columns are emitted, not only individuals.
- Thai text in KBank PDFs has tone marks/vowels reordered (e.g. `ประจา` for `ประจำ`), so labels are
  matched on a "skeleton" (marks removed). Product names come from the regex list in the YAML.
- The **non_resident** column has no numbers — the PDF refers to condition 12 instead (NRBA/NRBS:
  generally no interest / same as resident with BOT approval). So no `non_resident` rows.
- A blank cell means no row (e.g. 24/36-month fixed has no `special_juristic_2` rate).
- Conditions/footnotes (pages 5+) are not parsed.

## Sources
- KBank: https://www.kasikornbank.com/th/rate/pages/deposits.aspx
  (PDFs: `/th/rate/deposits/DDMMYYYY-deposit-rates-th.pdf`)

## Adding a bank
1. `rules/<bank>.yaml` — listing URL, link selector, cookie text, PDF layout hints.
2. `banks/<bank>.py` — `fetch_latest()` + `parse_pdf()` returning rows with the CSV fields;
   reuse `banks.common.browser_fetch`, `skeleton`, `upsert_rows`.
3. Commit the PDF in `raw/<bank>/` and add a test with a few known rates.
