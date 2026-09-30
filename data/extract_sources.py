#!/usr/bin/env python3
"""Extract text from the CMS PDFs into data/sources/*.txt.

Only the extracted text and its metadata are meant to be committed -- never the
PDFs themselves (see SCOPE.md section 9).

Usage (from the repo root):
    python3 data/extract_sources.py /path/to/folder/with/the/pdfs
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    sys.exit("Missing dependency: pip3 install -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent

# CMS product number -> (title, edition, plan years). Mirrors the table in SCOPE.md section 5.
META = {
    "10050": ("Medicare & You", "2027 handbook", "Mostly 2027, some 2026 figures"),
    "11095": ("Get Ready for Medicare", "Jan 2026", "2026"),
    "11389": ("Medicare: Getting Started", "Sept 2025", "2026"),
    "11579": ("2026 Medicare Costs", "Dec 2025", "2026"),
}


def clean(text: str) -> str:
    text = text.replace("\r", "")
    text = text.replace("\x02", "-").replace("­", "-")  # stray hyphen glyphs from the PDFs
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(pdf: Path, out_dir: Path) -> Path:
    m = re.match(r"(\d{5})", pdf.name)
    product = m.group(1) if m else "unknown"
    title, edition, years = META.get(product, (pdf.stem, "unknown", "unknown"))
    reader = PdfReader(str(pdf))
    total = len(reader.pages)

    header = [
        f"SOURCE: {title}",
        f"CMS PRODUCT: {product}",
        f"EDITION: {edition}",
        f"PLAN YEARS: {years}",
        'NOTE: Each page starts with a "[PDF page N of M]" marker. The printed page number '
        "appears inside the page text and can differ from N; cite the printed number.",
        "",
    ]
    body = []
    for i, page in enumerate(reader.pages, 1):
        body.append(f"[PDF page {i} of {total}]\n{clean(page.extract_text() or '')}\n")

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    out = out_dir / f"{product}-{slug}.txt"
    out.write_text("\n".join(header + body), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf_dir", type=Path, help="folder containing the CMS PDFs")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "sources")
    args = ap.parse_args()

    pdfs = sorted(args.pdf_dir.expanduser().glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {args.pdf_dir}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        out = extract(pdf, args.out_dir)
        print(f"{pdf.name} -> {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} ({out.stat().st_size:,} bytes)")
    print("Done. Review the .txt files, then commit them (not the PDFs).")


if __name__ == "__main__":
    main()
