#!/usr/bin/env python3
"""
Enrich hardware_data.json by adding a short_description for each product.

It downloads each product's PDF (from the existing `pdf_url` field), extracts
text from the first pages, and heuristically selects a concise paragraph to use
as a short description. If extraction fails, it falls back to generating a
compact description from existing JSON attributes.

Usage:
  python scripts/enrich_descriptions.py \
      --in data/hardware_data.json \
      --out data/hardware_data.json   # in-place (a .bak is written)

Optional flags:
  --skip-existing    Skip items that already have short_description.
  --max-pages N      Limit PDF text extraction to the first N pages (default 2).
  --timeout SEC      Network timeout per PDF (default 25).
  --delay SEC        Delay between downloads to be polite (default 0.4).

Requires:
  - pdfminer.six (for PDF text extraction)

Notes:
  - This script is resilient: it logs and continues on errors.
  - It writes a backup file when output path equals input path.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, Any, Optional


def _import_pdfminer():
    try:
        from pdfminer.high_level import extract_text
        from pdfminer.layout import LAParams
        return extract_text, LAParams
    except Exception as e:
        print("ERROR: pdfminer.six is required. Install with: pip install pdfminer.six", file=sys.stderr)
        raise


def load_json(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(obj: Dict[str, Any], path: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


@dataclass
class ExtractOptions:
    max_pages: int = 2
    timeout: int = 25
    delay: float = 0.4


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch_pdf(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def normalize_ws(s: str) -> str:
    s = s.replace("\u00a0", " ")  # nbsp
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def split_paragraphs(text: str) -> list[str]:
    # Break on blank lines or obvious separators
    parts = re.split(r"\n\s*\n+|\r\n\r\n+", text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Flatten internal newlines within a paragraph
        p = normalize_ws(p.replace("\n", " ").replace("\r", " "))
        out.append(p)
    return out


def is_valid_desc(p: str, product_name: str) -> bool:
    if len(p) < 40 or len(p) > 600:
        return False
    # Avoid mostly numeric/spec lines
    digits = sum(c.isdigit() for c in p)
    if digits / max(len(p), 1) > 0.30:
        return False
    # Avoid headings and obvious non-descriptive sections
    banned_prefixes = (
        "technical specifications",
        "specifications",
        "feature",
        "features",
        "hardware",
        "interfaces",
        "dimensions",
        "package",
        "warranty",
        "product code",
        "peplink",
        "pepwave",
        "datasheet",
        "max",
        "balance",
    )
    l = p.lower().strip()
    # Reject footnotes and bracketed annotations like [1]
    if l.startswith("["):
        return False
    if re.match(r"^\[\d+\]", l):
        return False
    if any(kw in l for kw in (
        "wan port(s) can be configured",
        "configured as a lan",
        "ge ports can be configured",
        "configured as wan",
        "lan 1-2 are configured as",
        "wan 3 is configured",
    )):
        return False
    if any(l.startswith(bp) for bp in banned_prefixes):
        return False
    # Avoid all caps paragraphs
    letters = [c for c in p if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return False
    # Allow if it mentions product family keywords or contains verbs
    verbs = ("is ", "provides", "delivers", "offers", "enables", "designed", "ideal", "supports")
    if any(v in l for v in verbs):
        return True
    # Fallback: accept if it mentions product name and contains spaces
    if product_name and product_name.split()[0].lower() in l:
        return True
    # Otherwise require several words
    if len(p.split()) >= 8:
        return True
    return False


def pick_short_description(text: str, product_name: str) -> Optional[str]:
    # Prefer early paragraphs
    paras = split_paragraphs(text)
    for p in paras[:8]:
        if is_valid_desc(p, product_name):
            # Clean weird hyphenations and spacing
            p = re.sub(r"\s+-\s+", "-", p)
            p = normalize_ws(p)
            return p[:240].rstrip() + ("…" if len(p) > 240 else "")
    return None


def extract_text_from_pdf_bytes(data: bytes, max_pages: int) -> str:
    extract_text, LAParams = _import_pdfminer()
    laparams = LAParams()
    # Use a BytesIO stream
    bio = io.BytesIO(data)
    # pdfminer doesn't expose an easy pages limit in extract_text; we rely on default
    # to process the whole doc and trim afterwards.
    raw = extract_text(bio, laparams=laparams)
    # Keep first N pages by splitting on \f (form feed) which pdfminer uses
    pages = raw.split("\f")
    raw_limited = "\n\n".join(pages[:max_pages])
    return raw_limited


def generate_from_attributes(name: str, pdata: Dict[str, Any]) -> str:
    # Build a compact synthetic description from key attributes
    parts = []
    modem = str(pdata.get("Number of Cellular Modems", "")).strip()
    users = str(pdata.get("Number of Recommended Users", "")).strip()
    tput = str(pdata.get("Router Throughput", "")).strip()
    wan = str(pdata.get("Number of Ethernet WAN ports", "")).strip()
    lan = str(pdata.get("Number of Ethernet LAN ports", "")).strip()
    wifi_ap = str(pdata.get("Wi‑Fi AP", pdata.get("Wi-Fi AP", ""))).strip()
    wifi_radio = str(pdata.get("Wi‑Fi Radio", pdata.get("Wi-Fi Radio", ""))).strip()
    g5 = str(pdata.get("5G support", "")).strip()

    if g5.lower() in ("yes", "true"): parts.append("5G")
    if modem and modem not in ("None", "0"): parts.append(f"{modem} modem")
    if wifi_ap and wifi_ap.lower() not in ("no", "none"): parts.append("Wi‑Fi AP")
    if wifi_radio: parts.append(wifi_radio)
    role = ", ".join(parts) if parts else "Router"

    details = []
    if wan: details.append(f"WAN: {wan}")
    if lan: details.append(f"LAN: {lan}")
    if tput: details.append(f"Throughput: {tput}")
    if users: details.append(f"Users: {users}")

    return normalize_ws(f"{name}: {role}. " + "; ".join(details))[:240]


def enrich(data: Dict[str, Any], opts: ExtractOptions, skip_existing: bool = True) -> Dict[str, Any]:
    total = 0
    added = 0
    for category, items in data.items():
        if not isinstance(items, dict):
            continue
        for name, pdata in items.items():
            total += 1
            if not isinstance(pdata, dict):
                continue
            if skip_existing and pdata.get("short_description"):
                continue
            url = pdata.get("pdf_url")
            short: Optional[str] = None
            if url:
                try:
                    blob = fetch_pdf(url, timeout=opts.timeout)
                    text = extract_text_from_pdf_bytes(blob, max_pages=opts.max_pages)
                    short = pick_short_description(text, name)
                    time.sleep(opts.delay)
                except Exception as e:
                    print(f"[warn] {name}: failed to extract from PDF ({e})")
            if not short:
                short = generate_from_attributes(name, pdata)
            if short:
                pdata["short_description"] = short
                added += 1
    print(f"Processed {total} items; updated {added} short_description fields.")
    return data


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Enrich hardware JSON with short descriptions from PDFs.")
    p.add_argument("--in", dest="inp", default="data/hardware_data.json", help="Input JSON path")
    p.add_argument("--out", dest="out", default=None, help="Output JSON path (default: same as input)")
    p.add_argument("--skip-existing", action="store_true", help="Skip items that already have descriptions")
    p.add_argument("--max-pages", type=int, default=2, help="Max pages to extract from each PDF")
    p.add_argument("--timeout", type=int, default=25, help="Timeout per PDF in seconds")
    p.add_argument("--delay", type=float, default=0.4, help="Delay between downloads in seconds")
    args = p.parse_args(argv)

    inp = args.inp
    out = args.out or inp
    same = os.path.abspath(inp) == os.path.abspath(out)

    data = load_json(inp)
    enriched = enrich(data, ExtractOptions(args.max_pages, args.timeout, args.delay), skip_existing=args.skip_existing)

    if same:
        bak = inp + ".bak"
        with contextlib.suppress(Exception):
            if os.path.exists(bak):
                os.remove(bak)
        os.replace(inp, bak)
        save_json(enriched, out)
        print(f"Wrote updated JSON in-place. Backup saved to: {bak}")
    else:
        save_json(enriched, out)
        print(f"Wrote enriched JSON to: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
