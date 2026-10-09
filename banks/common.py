"""Shared helpers for bank deposit-rate scrapers."""
from __future__ import annotations

import base64
import csv
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_CSV = ROOT / "data" / "rates.csv"
FIELDS = ["bank", "effective_date", "product", "term", "amount_band",
          "customer_type", "rate_pct", "source_pdf"]

# Thai marks that the PDF text layer tends to reorder or split:
# mai han-akat, above/below vowels, phinthu, maitaikhu, tone marks, thanthakhat...
_THAI_MARKS = re.compile("[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]")

THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


def skeleton(text: str) -> str:
    """Normalise Thai text for robust matching: drop whitespace and marks, ำ -> า."""
    text = text.replace("\u0E33", "\u0E32")
    text = _THAI_MARKS.sub("", text)
    return re.sub(r"\s+", "", text)


MONTH_SKELETONS = {skeleton(m): i + 1 for i, m in enumerate(THAI_MONTHS)}


def thai_date_to_iso(day: str, month_skel: str, year_be: str) -> str | None:
    month = MONTH_SKELETONS.get(month_skel)
    if not month:
        return None
    return f"{int(year_be) - 543:04d}-{month:02d}-{int(day):02d}"


def load_rules(bank: str) -> dict:
    with open(ROOT / "rules" / f"{bank}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def upsert_rows(rows: list[dict], bank: str, effective_date: str, path: Path = DATA_CSV) -> int:
    """Replace all rows for (bank, effective_date) with `rows`; keep the rest. Returns total rows."""
    existing: list[dict] = []
    if path.exists():
        with open(path, encoding="utf-8", newline="") as f:
            existing = [r for r in csv.DictReader(f)
                        if not (r["bank"] == bank and r["effective_date"] == effective_date)]
    allrows = existing + rows
    allrows.sort(key=lambda r: (r["bank"], r["effective_date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(allrows)
    return len(allrows)


def browser_fetch(listing_url: str, link_selector: str, cfg: dict, pick) -> tuple[str, bytes]:
    """Open `listing_url` in headless Playwright Chromium, accept cookies, collect PDF
    links matching `link_selector`, choose one with `pick(hrefs) -> href`, and download
    it from inside the page (same cookies / bot-check session). Returns (url, bytes)."""
    from playwright.sync_api import sync_playwright

    last_err: Exception | None = None
    with sync_playwright() as p:
        for attempt in range(int(cfg.get("retries", 3))):
            browser = p.chromium.launch(headless=True, channel=cfg.get("channel", "chromium"),
                                        args=["--disable-blink-features=AutomationControlled"])
            try:
                ctx = browser.new_context(locale=cfg.get("locale", "th-TH"),
                                          user_agent=cfg.get("user_agent"),
                                          viewport={"width": 1366, "height": 900})
                page = ctx.new_page()
                resp = page.goto(listing_url, wait_until="domcontentloaded", timeout=90_000)
                if resp is None or resp.status != 200:
                    raise RuntimeError(f"listing HTTP {resp.status if resp else '??'} ({page.title()})")
                accept = cfg.get("cookie_accept_text")
                if accept:
                    try:
                        page.get_by_text(accept, exact=True).first.click(timeout=8_000)
                    except Exception:
                        pass  # banner absent or already accepted
                page.wait_for_selector(link_selector, state="attached", timeout=45_000)
                hrefs = page.eval_on_selector_all(link_selector, "els => els.map(e => e.href)")
                url = pick(sorted(set(hrefs)))
                b64 = page.evaluate(
                    """async (u) => {
                        const r = await fetch(u, {credentials: 'include'});
                        if (!r.ok) throw new Error('HTTP ' + r.status);
                        const buf = new Uint8Array(await r.arrayBuffer());
                        let s = ''; for (let i = 0; i < buf.length; i += 0x8000)
                            s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
                        return btoa(s);
                    }""", url)
                data = base64.b64decode(b64)
                if not data.startswith(b"%PDF"):
                    raise RuntimeError("downloaded file is not a PDF")
                return url, data
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"  fetch attempt {attempt + 1} failed: {e}")
            finally:
                browser.close()
    raise RuntimeError(f"browser fetch failed: {last_err}")
