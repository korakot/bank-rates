"""KBank (Kasikornbank) deposit-rate scraper.

    python -m banks.kbank                 # fetch latest PDF (headless Playwright), parse, update data/rates.csv
    python -m banks.kbank --pdf FILE.pdf  # use a PDF you downloaded yourself (fallback if the site blocks us)
    python -m banks.kbank --reparse       # re-parse every PDF already in raw/kbank/
    python -m banks.kbank --pdf FILE.pdf --dry-run   # print rows, write nothing
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import pdfplumber

from banks.common import ROOT, browser_fetch, load_rules, skeleton, thai_date_to_iso, upsert_rows

BANK = "kbank"
RATE_RE = re.compile(r"^\d\.\d{3,4}$")
SK = skeleton  # short alias


# --------------------------------------------------------------------------- fetch
def fetch_latest(rules: dict) -> tuple[str, bytes, str]:
    lst = rules["listing"]
    link_re = re.compile(lst["pdf_link_regex"])

    def link_date(href: str) -> str:
        m = link_re.search(href)
        d = m.group(1) if m else "00000000"
        return f"{d[4:8]}-{d[2:4]}-{d[0:2]}"  # DDMMYYYY -> YYYY-MM-DD

    def pick(hrefs: list[str]) -> str:
        hrefs = [h for h in hrefs if link_re.search(h)]
        if not hrefs:
            raise RuntimeError("no deposit-rate PDF links found on listing page")
        print("  found:", ", ".join(sorted({link_date(h) for h in hrefs}, reverse=True)))
        return max(hrefs, key=link_date)

    url, data = browser_fetch(lst["url"], lst["pdf_link_selector"], rules["fetch"], pick)
    return url, data, link_date(url)


# --------------------------------------------------------------------------- parse
def amount_band(label_sk: str) -> str:
    def n(x: str) -> str:
        v = float(x.replace(",", ""))
        return f"{v:g}"
    pats = [
        (r"นอยกวา([\d,.]+)ลานบาท", lambda m: f"<{n(m[1])}M"),
        (r"ตงแต([\d,.]+)ลานบาทแตไมถง([\d,.]+)ลานบาท", lambda m: f"{n(m[1])}M-<{n(m[2])}M"),
        (r"ตงแต([\d,.]+)ลานบาทแตไมเกน([\d,.]+)ลานบาท", lambda m: f"{n(m[1])}M-{n(m[2])}M"),
        (r"ตงแต([\d,.]+)ลานบาทขนไป", lambda m: f">={n(m[1])}M"),
        (r"สวนทเกน([\d,.]+)แสนบาท", lambda m: f">{n(str(float(m[1]) * 100))}k"),
        (r"ไมเกน([\d,.]+)แสนบาท", lambda m: f"<={n(str(float(m[1]) * 100))}k"),
        (r"วงเงน([\d,]+)-([\d,]+)บาท", lambda m: f"{n(m[1])}-{n(m[2])} THB/month"),
    ]
    for pat, fmt in pats:
        m = re.search(pat, label_sk)
        if m:
            return fmt(m)
    return label_sk.removeprefix("วงเงน") or "all"


def term_from(text_sk: str) -> str | None:
    if m := re.search(r"ระยะเวลาฝาก(\d+)[–-](\d+)วน", text_sk):
        return f"{m[1]}-{m[2]}D"
    if m := re.search(r"ฝากตงแต(\d+)วน", text_sk):
        return f">={m[1]}D"
    if m := re.search(r"(\d+)เดอน", text_sk):
        return f"{m[1]}M"
    return None


def classify(header_sk: str, products: list[tuple[re.Pattern, str]]) -> str:
    for rx, pid in products:
        if m := rx.search(header_sk):
            return pid.replace("{1}", m.group(1)) if "{1}" in pid else pid
    return "unknown:" + header_sk[:40]


def page_lines(page, label_max_x: float):
    """Group words into visual lines -> list of (top, label_text, [(x_centre, rate_str)])."""
    words = page.extract_words(x_tolerance=1.5)
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lines: list[list] = []
    for w in words:
        if lines and abs(lines[-1][0] - w["top"]) < 3:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    out = []
    for top, ws in lines:
        ws.sort(key=lambda w: w["x0"])
        label = " ".join(w["text"] for w in ws if w["x1"] < label_max_x)
        rates = [((w["x0"] + w["x1"]) / 2, w["text"]) for w in ws
                 if w["x0"] > label_max_x - 5 and RATE_RE.match(w["text"])]
        out.append([top, label, rates])
    return out


def attach_orphans(lines):
    """Some rates sit a few pt above/below their 'วงเงิน' label. Move them onto it."""
    for i, (top, label, rates) in enumerate(lines):
        if rates and not label.strip():
            cands = [j for j in range(max(0, i - 2), min(len(lines), i + 3))
                     if j != i and not lines[j][2] and SK(lines[j][1]).startswith("วงเงน")
                     and abs(lines[j][0] - top) <= 12]
            if cands:
                j = min(cands, key=lambda j: abs(lines[j][0] - top))
                lines[j][2] = rates
                lines[i][2] = []
    return [ln for ln in lines if ln[1].strip() or ln[2]]


def effective_date_from(pdf) -> str | None:
    sk = SK(pdf.pages[0].extract_text() or "")
    m = re.search(r"เรมใชตงแตวนท(\d{1,2})(\D+?)(\d{4})", sk)
    return thai_date_to_iso(m[1], m[2], m[3]) if m else None


def parse_pdf(path: Path, rules: dict, source_name: str) -> tuple[str, list[dict]]:
    cfg = rules["pdf"]
    columns = [(c["x"], c["customer_type"]) for c in cfg["columns"]]
    tol = cfg["column_tolerance"]
    products = [(re.compile(SK(p["match"])), p["id"]) for p in rules["products"]]

    rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        eff = effective_date_from(pdf)
        if not eff:
            raise ValueError("could not find effective date (เริ่มใช้ตั้งแต่วันที่ ...) on page 1")
        header, frozen, product, term = "", False, None, ""
        for pi in cfg["table_pages"]:
            for top, label, rates in attach_orphans(page_lines(pdf.pages[pi], cfg["label_max_x"])):
                sk = SK(label)
                if re.match(r"^\d+\.", sk):                       # new numbered section
                    header, frozen, product = sk, False, None
                    term = term_from(sk) or ""
                elif sk.startswith("เงนฝาก") and not rates:          # unnumbered sub-section
                    header, frozen, product = sk, False, None
                    term = term_from(sk) or ""
                elif re.match(r"^(ระยะเวลาฝาก|ฝากตงแต)", sk):          # term line
                    term = term_from(sk) or term
                    continue
                elif not frozen and not sk.startswith(("วงเงน", "(")) and not rates and sk \
                        and header and not re.match(r"^(ทอ|ประเภท)", sk):
                    header += sk                                     # heading continuation
                if not rates:
                    continue
                if not frozen:
                    probe = header + ("" if sk.startswith("วงเงน") or sk == header else sk)
                    product, frozen = classify(probe, products), True
                band = amount_band(sk) if sk.startswith("วงเงน") else "all"
                for x, val in rates:
                    col = min(columns, key=lambda c: abs(c[0] - x))
                    if abs(col[0] - x) > tol:
                        print(f"  warn: rate {val} at x={x:.0f} matches no column ({label})", file=sys.stderr)
                        continue
                    rows.append({
                        "bank": BANK, "effective_date": eff, "product": product, "term": term,
                        "amount_band": band, "customer_type": col[1],
                        "rate_pct": f"{float(val):.3f}", "source_pdf": source_name,
                    })
    return eff, rows


# --------------------------------------------------------------------------- main
def process(pdf_path: Path, rules: dict, dry_run: bool, expected_date: str | None = None) -> None:
    eff, _ = parse_pdf(pdf_path, rules, "")
    if expected_date and expected_date != eff:
        print(f"  note: link date {expected_date} != PDF effective date {eff}; using PDF date")
    dest = ROOT / rules["raw_path"].format(date=eff)
    rel = dest.relative_to(ROOT).as_posix()
    eff, rows = parse_pdf(pdf_path, rules, rel)
    print(f"  {pdf_path.name}: effective {eff}, {len(rows)} rows")
    if dry_run:
        for r in rows:
            print("   ", ",".join(r.values()))
        return
    if pdf_path.resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf_path, dest)
        print(f"  saved {rel}")
    total = upsert_rows(rows, BANK, eff)
    print(f"  data/rates.csv now has {total} rows")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", type=Path, help="parse this local PDF instead of fetching")
    ap.add_argument("--reparse", action="store_true", help="re-parse all PDFs in raw/kbank/")
    ap.add_argument("--dry-run", action="store_true", help="print rows, write nothing")
    args = ap.parse_args(argv)
    rules = load_rules(BANK)

    if args.reparse:
        for p in sorted((ROOT / "raw" / BANK).glob("*.pdf")):
            process(p, rules, args.dry_run)
        return 0
    if args.pdf:
        process(args.pdf, rules, args.dry_run)
        return 0

    print(f"fetching {rules['listing']['url']} (headless Chromium)")
    try:
        url, data, link_date = fetch_latest(rules)
    except Exception as e:  # noqa: BLE001
        print(f"fetch failed: {e}\nDownload the PDF manually and run: python -m banks.kbank --pdf FILE.pdf",
              file=sys.stderr)
        return 2
    print(f"  downloaded {url} ({len(data):,} bytes)")
    dest = ROOT / rules["raw_path"].format(date=link_date)
    if dest.exists() and dest.read_bytes() == data:
        print(f"  unchanged: {dest.relative_to(ROOT)} already up to date")
    tmp = ROOT / "raw" / BANK / ".download.pdf"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        process(tmp, rules, args.dry_run, expected_date=link_date)
    finally:
        tmp.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
