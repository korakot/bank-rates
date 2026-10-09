"""Checks the parser against known individual rates in the 9 May 2026 KBank PDF.
Run:  python -m pytest -q   (or: python tests/test_kbank.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from banks.common import ROOT, load_rules  # noqa: E402
from banks.kbank import parse_pdf  # noqa: E402

PDF = ROOT / "raw" / "kbank" / "2026-05-09-th.pdf"

EXPECTED_INDIVIDUAL = {
    ("current", "", "all"): "0.000",
    ("savings", "", "<1M"): "0.250",
    ("k-esavings", "", "<=500k"): "1.250",
    ("k-esavings", "", ">500k"): "0.350",
    ("k-esavings-make", "", "<=500k"): "1.250",
    ("k-epocket", "", ">500k"): "0.350",
    ("fixed", "3M", "<10M"): "0.550",
    ("fixed", "6M", "<10M"): "0.600",
    ("fixed", "12M", "<10M"): "0.750",
    ("fixed", "24M", "<10M"): "0.900",
    ("fixed", "36M", "<10M"): "0.900",
    ("super-senior", "", "0.1M-3M"): "0.900",
    ("taweesap", "24M", "500-25000 THB/month"): "1.600",
}


def test_kbank_2026_05_09():
    eff, rows = parse_pdf(PDF, load_rules("kbank"), "raw/kbank/2026-05-09-th.pdf")
    assert eff == "2026-05-09"
    ind = {(r["product"], r["term"], r["amount_band"]): r["rate_pct"]
           for r in rows if r["customer_type"] == "individual"}
    for key, rate in EXPECTED_INDIVIDUAL.items():
        assert ind.get(key) == rate, (key, ind.get(key), rate)
    # every fixed-deposit term has 5 amount bands
    for t in ["3M", "6M", "12M", "24M", "36M"]:
        assert sum(1 for k in ind if k[0] == "fixed" and k[1] == t) == 5, t
    assert not [r for r in rows if r["product"].startswith("unknown")]
    # spot-check other customer columns
    other = {(r["product"], r["term"], r["amount_band"], r["customer_type"]): r["rate_pct"] for r in rows}
    assert other[("fixed", "12M", "<10M", "juristic_1")] == "0.400"
    assert other[("fixed", "3M", ">=500M", "fund")] == "0.300"
    assert other[("savings-special-juristic", ">=30D", ">=500M", "special_juristic_1")] == "0.450"


if __name__ == "__main__":
    test_kbank_2026_05_09()
    print("ok")
