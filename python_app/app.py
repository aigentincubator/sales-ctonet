import json
import os
from copy import deepcopy
from urllib.parse import urlencode
from typing import Optional, List, Tuple, Dict

from flask import Flask, render_template, request, url_for, g
from werkzeug.middleware.proxy_fix import ProxyFix


app = Flask(__name__)
# Respect reverse proxy headers so the app works under a subpath and HTTPS
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Default to repo-level data file: ../data/hardware_data.json
DATA_PATH = os.environ.get(
    "PEPLINK_DATA_PATH",
    os.path.normpath(os.path.join(BASE_DIR, "..", "data", "hardware_data.json")),
)


def load_data(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


RAW = load_data(DATA_PATH)
ALL_CATEGORIES = list(RAW.keys())
SERIES_ATTR = "Series"


CLIENT_CATEGORIES = [
    "Essential",
    "Business",
    "Enterprise",
]
PRICE_MATRIX_KEY = "_client_price_matrix"


def _build_mock_price_db(raw_data: dict) -> dict[str, dict[str, dict[str, int]]]:
    """Create deterministic mock pricing tiers for every hardware entry."""
    price_db: dict[str, dict[str, dict[str, int]]] = {}
    for cat_idx, (category, products) in enumerate(raw_data.items()):
        cat_prices: dict[str, dict[str, int]] = {}
        for prod_idx, (product_name, _) in enumerate(sorted(products.items(), key=lambda x: x[0])):
            base = 800 + cat_idx * 220 + prod_idx * 25
            cat_prices[product_name] = {
                CLIENT_CATEGORIES[0]: base,
                CLIENT_CATEGORIES[1]: base + 190,
                CLIENT_CATEGORIES[2]: base + 340,
            }
        price_db[category] = cat_prices
    return price_db


PRICE_DB = _build_mock_price_db(RAW)


@app.context_processor
def inject_embed_flag():
    val = str(request.args.get("embed", "")).lower()
    is_embed = val in ("1", "true", "yes")
    return {"embed": is_embed}


@app.after_request
def add_embed_csp(resp):
    # Allow embedding this tool on peplink.com domains (iframe integration)
    csp = resp.headers.get("Content-Security-Policy")
    allow = "frame-ancestors 'self' https://peplink.com https://www.peplink.com https://*.peplink.com"
    if csp:
        # If a CSP already exists, append frame-ancestors if not present
        if 'frame-ancestors' not in csp:
            resp.headers['Content-Security-Policy'] = csp.rstrip(';') + '; ' + allow
    else:
        resp.headers['Content-Security-Policy'] = allow
    return resp


@app.template_global()
def clear_all_url() -> str:
    return url_for("index")


def parse_filters(args) -> dict:
    filters = {}
    for k in args.keys():
        if k in {"category", "client_category", "short_description", "min_router_mbps", "min_speedfusion_mbps", "min_users", "sort", "embed"}:
            continue
        vals = args.getlist(k)
        # normalize to strings
        vals = [str(v) for v in vals if v is not None]
        if vals:
            filters[k] = [vals[0]]
    return filters


@app.template_global()
def build_url(category: Optional[str], filters: dict, client_category: Optional[str] = None) -> str:
    # Preserve numeric filters and sort from current request if present
    preserve_keys = {"min_router_mbps", "min_speedfusion_mbps", "min_users", "sort", "embed", "client_category"}
    qs = {k: request.args.get(k) for k in preserve_keys if request.args.get(k) not in (None, "")}
    if client_category is None:
        client_category = qs.get("client_category")
    if client_category not in CLIENT_CATEGORIES:
        client_category = None
    if client_category:
        qs["client_category"] = client_category
    elif "client_category" in qs:
        del qs["client_category"]
    if category:
        qs["category"] = category
    for k, vals in filters.items():
        qs[k] = list(vals)
    # Use doseq=True to repeat keys for multi-value attributes
    return url_for("index") + ("?" + urlencode(qs, doseq=True) if qs else "")


# ---- Display helpers ----
def _normalize_display_value(s: str) -> str:
    """Normalize odd unicode spaces/dashes for cleaner display and copying.

    - Replace NBSP with regular space
    - Replace en/em dashes with hyphen
    - Collapse repeated spaces
    """
    if s is None:
        return ""
    out = str(s).replace("\xa0", " ")
    out = out.replace("\u2013", "-").replace("\u2014", "-")
    # collapse whitespace
    out = " ".join(out.split())
    return out


@app.template_filter("norm")
def jinja_norm_filter(s: str) -> str:
    return _normalize_display_value(s)


@app.template_global()
def set_category_url(cat: str) -> str:
    client_cat = request.args.get("client_category")
    return build_url(cat, {}, client_cat)


@app.template_global()
def set_client_category_url(client_cat: str) -> str:
    category = request.args.get("category")
    filters = parse_filters(request.args)
    return build_url(category, filters, client_cat)


@app.template_global()
def toggle_filter_url(attr: str, value: str) -> str:
    category = request.args.get("category")
    current = parse_filters(request.args)
    sval = str(value)
    quick_pick_attrs = getattr(g, "quick_pick_attrs", set())
    existing = current.get(attr, [])
    if sval in existing and len(existing) == 1:
        current.pop(attr, None)
    else:
        current[attr] = [sval]
        if attr in quick_pick_attrs:
            for qp_attr in quick_pick_attrs:
                if qp_attr != attr:
                    current.pop(qp_attr, None)
    return build_url(category, current)


def get_products_in_category(category: Optional[str]):
    if not category or category not in RAW:
        return []
    items = RAW.get(category, {}) or {}
    return [
        (name, augment_product_data(category, name, dict(data)))
        for name, data in items.items()
    ]


def _parse_int_prefix(value: str) -> int:
    if not isinstance(value, str):
        return 0
    # Extract first integer from strings like "2 (5G)" or "1"; return 0 if none
    num = ''
    for ch in value:
        if ch.isdigit():
            num += ch
        elif num:
            break
    try:
        return int(num) if num else 0
    except Exception:
        return 0


def _yes_no(val: str) -> str:
    s = str(val).strip().lower()
    if s.startswith("y"):
        return "Yes"
    if s.startswith("n"):
        return "No"
    return val


def format_price(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-6:
        return f"${int(round(number)):,}"
    return f"${number:,.2f}"


def augment_product_data(category: str, name: str, pdata: dict) -> dict:
    """Add derived/normalized fields used for better filtering.

    - Adds "Modem Group": Single / Multi / None based on "Number of Cellular Modems"
    - Normalizes some Yes/No style fields
    - Unifies SpeedFusion throughput key variants
    """
    out = dict(pdata)

    # Attach mock pricing matrix for client categories
    price_map = PRICE_DB.get(category, {}).get(name)
    if price_map:
        out[PRICE_MATRIX_KEY] = price_map

    # Derive modem group
    modem_raw = out.get("Number of Cellular Modems")
    count = _parse_int_prefix(str(modem_raw)) if modem_raw is not None else 0
    if count <= 0:
        modem_group = "None"
    elif count == 1:
        modem_group = "Single"
    else:
        modem_group = "Multi"
    out["Modem Group"] = modem_group

    # Normalize Yes/No style flags we expose prominently
    if "Wi‑Fi AP" in out:
        out["Wi‑Fi AP"] = _yes_no(out["Wi‑Fi AP"])
    if "5G support" in out:
        out["5G support"] = _yes_no(out["5G support"])
    # Infer 5G support from related fields if missing or inconsistent
    try:
        fields_to_scan = [
            str(out.get("Number of Cellular Modems", "")),
            str(out.get("Cellular Modem Category options", "")),
            str(out.get("short_description", "")),
        ]
        has_5g = any("5g" in s.lower() for s in fields_to_scan)
        cur_5g = out.get("5G support")
        if has_5g and cur_5g != "Yes":
            out["5G support"] = "Yes"
        elif not has_5g and (cur_5g is None or str(cur_5g).strip() == ""):
            # Only set to No if not already specified
            out["5G support"] = "No"
    except Exception:
        pass

    # Unify SpeedFusion throughput key naming (no encryption)
    if (
        "SpeedFusion Throughput (no encryption)" not in out
        and "SpeedFusion VPN Throughput (No Encryption)" in out
    ):
        out["SpeedFusion Throughput (no encryption)"] = out[
            "SpeedFusion VPN Throughput (No Encryption)"
        ]
    # And for the encrypted variant (keep original too)
    if (
        "SpeedFusion Throughput (256‑bit AES)" not in out
        and "SpeedFusion VPN Throughput (256‑bit AES)" in out
    ):
        out["SpeedFusion Throughput (256‑bit AES)"] = out[
            "SpeedFusion VPN Throughput (256‑bit AES)"
        ]

    # Numeric derivations for sorting and range filters
    if "Router Throughput" in out:
        out["Router Throughput (Mbps)"] = _parse_mbps(out["Router Throughput"])
    if "SpeedFusion Throughput (no encryption)" in out:
        out["SpeedFusion (Mbps)"] = _parse_mbps(out["SpeedFusion Throughput (no encryption)"])
    if "Number of Recommended Users" in out:
        lo, hi = _parse_users_range(out["Number of Recommended Users"])
        out["Users Min"], out["Users Max"] = lo, hi
    if "Number of Ethernet WAN ports" in out:
        out["WAN Ports Max"] = max([_parse_int_prefix(tok)
                                     for tok in _strip_nbsp(out["Number of Ethernet WAN ports"]).replace('or', '/').split('/')])
    if "Number of Ethernet LAN ports" in out:
        out["LAN Ports Max"] = max([_parse_int_prefix(tok)
                                     for tok in _strip_nbsp(out["Number of Ethernet LAN ports"]).replace('or', '/').split('/')])

    return out


def parse_numeric_filters(args) -> dict:
    def to_int(name: str) -> Optional[int]:
        v = args.get(name)
        if v is None or v == "":
            return None
        try:
            return int(str(v))
        except Exception:
            return None

    return {
        "min_router_mbps": to_int("min_router_mbps"),
        "min_speedfusion_mbps": to_int("min_speedfusion_mbps"),
        "min_users": to_int("min_users"),
    }


def _strip_nbsp(s: str) -> str:
    return str(s).replace("\xa0", " ").strip()


def _parse_mbps(s: str) -> int:
    """Parse strings like '300 Mbps', '2.5 Gbps' into Mbps (int)."""
    if not s:
        return 0
    s = _strip_nbsp(s)
    # Extract first float number
    num = ''
    dot_seen = False
    for ch in s:
        if ch.isdigit():
            num += ch
        elif ch == '.' and not dot_seen:
            dot_seen = True
            num += ch
        elif num:
            break
    try:
        val = float(num) if num else 0.0
    except Exception:
        val = 0.0
    mult = 1
    low = s.lower()
    if 'gbps' in low or 'gbit' in low or ' 2.5 g' in low or 'g ' in low:
        mult = 1000
    # Default assume Mbps
    return int(round(val * mult))


def _parse_users_range(s: str) -> tuple[int, int]:
    """Parse '1–60' or '50–500' into (min,max). If single number, both equal."""
    if not s:
        return (0, 0)
    s = _strip_nbsp(s)
    # Normalize dashes
    s = s.replace('\u2013', '-').replace('\u2014', '-')
    parts = s.split('-')
    nums = []
    for part in parts:
        digits = ''.join(ch for ch in part if ch.isdigit())
        if digits:
            nums.append(int(digits))
    if not nums:
        # Try single number in whole string
        digits = ''.join(ch for ch in s if ch.isdigit())
        return (int(digits), int(digits)) if digits else (0, 0)
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (min(nums[0], nums[1]), max(nums[0], nums[1]))


def apply_filters(products: list[tuple[str, dict]], filters: dict, numeric_filters: Optional[dict] = None) -> list[tuple[str, dict]]:
    if not filters:
        filtered = products
    else:
        filtered = []
        for name, data in products:
            ok = True
            for attr, vals in filters.items():
                v = str(data.get(attr, ""))
                if v not in vals:
                    ok = False
                    break
            if ok:
                filtered.append((name, data))

    # Apply numeric constraints
    nf = numeric_filters or {}
    min_router = nf.get("min_router_mbps")
    min_sf = nf.get("min_speedfusion_mbps")
    min_users = nf.get("min_users")

    if min_router is not None:
        filtered = [(n, d) for (n, d) in filtered if int(d.get("Router Throughput (Mbps)", 0)) >= min_router]
    if min_sf is not None:
        filtered = [(n, d) for (n, d) in filtered if int(d.get("SpeedFusion (Mbps)", 0)) >= min_sf]
    if min_users is not None:
        filtered = [(n, d) for (n, d) in filtered if int(d.get("Users Max", 0)) >= min_users]

    return filtered


def build_attribute_index(products: list[tuple[str, dict]]):
    idx: dict[str, set[str]] = {}
    for _, pdata in products:
        for k, v in pdata.items():
            if k in (
                "Citations",
                "pdf_url",
                "short_description",
                # exclude derived numeric fields from attribute chips
                "Router Throughput (Mbps)",
                "SpeedFusion (Mbps)",
                "Users Min",
                "Users Max",
                "WAN Ports Max",
                "LAN Ports Max",
            ) or k == PRICE_MATRIX_KEY:
                continue
            s = idx.setdefault(k, set())
            s.add(str(v))
    return idx


def compute_salience(products_all: list[tuple[str, dict]]):
    idx = build_attribute_index(products_all)
    scores = []
    for attr, values in idx.items():
        distinct = len(values)
        if distinct <= 1:
            continue
        scores.append((attr, distinct))
    # sort by distinct value count desc, then lexicographically
    scores.sort(key=lambda x: (-x[1], x[0]))
    return [a for a, _ in scores]


def count_with_toggled(products_all, current_filters: dict, attr: str, val: str, numeric_filters: Optional[dict] = None) -> int:
    f = deepcopy(current_filters)
    sval = str(val)
    quick_pick_attrs = getattr(g, "quick_pick_attrs", set())
    existing = f.get(attr, [])
    if sval in existing and len(existing) == 1:
        f.pop(attr, None)
    else:
        f[attr] = [sval]
        if attr in quick_pick_attrs:
            for qp_attr in quick_pick_attrs:
                if qp_attr != attr:
                    f.pop(qp_attr, None)
    return len(apply_filters(products_all, f, numeric_filters))


def count_with_included(products_all, current_filters: dict, attr: str, val: str, numeric_filters: Optional[dict] = None) -> int:
    """Count results when the value is INCLUDED (non-toggling).

    This is used for subcategory chips so counts don't jump when a chip is already active.
    """
    f = deepcopy(current_filters)
    sval = str(val)
    quick_pick_attrs = getattr(g, "quick_pick_attrs", set())
    f[attr] = [sval]
    if attr in quick_pick_attrs:
        for qp_attr in quick_pick_attrs:
            if qp_attr != attr:
                f.pop(qp_attr, None)
    return len(apply_filters(products_all, f, numeric_filters))


# Note: use punctuation consistent with dataset keys (e.g., Wi‑Fi with NB hyphen)
SUMMARY_CANDIDATES = [
    # Priority view: WAN/LAN, Wi‑Fi, SpeedFusion (tunnels/throughput), Router throughput, Users
    "Number of Ethernet WAN ports",
    "Number of Ethernet LAN ports",
    "Wi‑Fi AP",
    "Wi‑Fi Radio",
    "SpeedFusion Throughput (no encryption)",
    "SpeedFusion VPN Throughput (No Encryption)",
    "Router Throughput",
    "Number of Recommended Users",
    # Helpful extras
    "5G support",
    "Number of Cellular Modems",
    "Modem Group",
    "SIM Slots",
    "Series",
]


def pick_summary_keys(pdata: dict) -> list[str]:
    # Ensure preferred SpeedFusion key selection (no duplication)
    sf_key = None
    if "SpeedFusion Throughput (no encryption)" in pdata:
        sf_key = "SpeedFusion Throughput (no encryption)"
    elif "SpeedFusion VPN Throughput (No Encryption)" in pdata:
        sf_key = "SpeedFusion VPN Throughput (No Encryption)"

    ordered: list[str] = []
    priority = [
        "Number of Ethernet WAN ports",
        "Number of Ethernet LAN ports",
        "Wi‑Fi AP",
        "Wi‑Fi Radio",
        sf_key,
        "Router Throughput",
        "Number of Recommended Users",
    ]
    for k in priority:
        if k and k in pdata and k not in ordered:
            ordered.append(k)

    extras = [
        "5G support",
        "Number of Cellular Modems",
        "Modem Group",
        "SIM Slots",
        "Series",
    ]
    for k in extras:
        if k in pdata and k not in ordered:
            ordered.append(k)

    if len(ordered) < 8:
        for k in pdata.keys():
            if k in ("Citations", "pdf_url", "short_description"):
                continue
            if k not in ordered:
                ordered.append(k)
            if len(ordered) >= 8:
                break
    return ordered[:8]


@app.route("/")
def index():
    category = request.args.get("category")
    filters = parse_filters(request.args)
    numeric_filters = parse_numeric_filters(request.args)
    sort_key = request.args.get("sort")
    client_category = request.args.get("client_category")
    if client_category not in CLIENT_CATEGORIES:
        client_category = CLIENT_CATEGORIES[0]

    products_all = get_products_in_category(category)
    salience_order = compute_salience(products_all) if category else []

    # Products visible with current filters
    products_filtered = apply_filters(products_all, filters, numeric_filters) if category else []

    # Attribute index based on all products in category (like the static MVP)
    attr_index_all = build_attribute_index(products_all) if category else {}

    # Subcategories (Series) available in this category
    subcategory_values = []
    if category:
        series_values = sorted(attr_index_all.get(SERIES_ATTR, set()), key=lambda s: s.lower())
        selected_series = set(filters.get(SERIES_ATTR, []))
        for val in series_values:
            # Use inclusion semantics so counts reflect adding this subcategory
            n = count_with_included(products_all, filters, SERIES_ATTR, val, numeric_filters)
            subcategory_values.append({
                "value": val,
                "count": n,
                "active": val in selected_series,
            })

    # Top attributes to show: prioritize the fields CTONET cares about
    PREFERRED_ATTR_ORDER = [
        "Number of Cellular Modems",
        "Modem Group",
        "5G support",
        "Wi‑Fi AP",
        "Wi‑Fi Radio",
        "Number of Ethernet WAN ports",
        "Number of Ethernet LAN ports",
        "Router Throughput",
        "SpeedFusion Throughput (no encryption)",
        "SpeedFusion VPN Throughput (No Encryption)",
        "SIM Slots",
        "Number of Recommended Users",
    ]
    existing_attrs = set(attr_index_all.keys())
    # Keep preferred attrs that exist, in order
    ordered = [a for a in PREFERRED_ATTR_ORDER if a in existing_attrs]
    # Fill the rest by salience
    for a in salience_order:
        if a == SERIES_ATTR:
            continue
        if a not in ordered:
            ordered.append(a)
        if len(ordered) >= 12:
            break
    top_attrs = ordered

    # For each attribute, build values and counts (how many remain if toggled)
    attributes = []
    if category:
        for attr in top_attrs:
            values_sorted = sorted(attr_index_all.get(attr, set()), key=lambda s: (s.lower()))
            selected = set(filters.get(attr, []))
            value_items = []
            for val in values_sorted:
                n = count_with_included(products_all, filters, attr, val, numeric_filters)
                value_items.append({
                    "value": val,
                    "count": n,
                    "active": val in selected,
                })
            attributes.append({
                "name": attr,
                "selected_count": len(selected),
                "options_count": len(values_sorted),
                "values": value_items,
            })

    # Quick picks for Mobile Routers (Single/Multi modem and 5G)
    quick_pick_attrs: set[str] = set()
    g.quick_pick_attrs = quick_pick_attrs
    quick_picks = []
    if category == "Mobile Routers":
        def add_quick(label: str, attr: str, val: str):
            if attr not in attr_index_all:
                return
            quick_pick_attrs.add(attr)
            quick_picks.append({
                "label": label,
                "attr": attr,
                "value": val,
                "count": count_with_included(products_all, filters, attr, val, numeric_filters),
                "active": val in set(filters.get(attr, [])),
            })

        add_quick("Single Modem", "Modem Group", "Single")
        add_quick("Multi Modem", "Modem Group", "Multi")
        add_quick("5G", "5G support", "Yes")

    # Sorting
    def sort_products(items: list[tuple[str, dict]], key: Optional[str]) -> list[tuple[str, dict]]:
        if not key:
            return sorted(items, key=lambda x: x[0].lower())  # default name asc for stability
        if key == "router_desc":
            return sorted(items, key=lambda x: (-int(x[1].get("Router Throughput (Mbps)", 0)), x[0].lower()))
        if key == "speedfusion_desc":
            return sorted(items, key=lambda x: (-int(x[1].get("SpeedFusion (Mbps)", 0)), x[0].lower()))
        if key == "users_desc":
            return sorted(items, key=lambda x: (-int(x[1].get("Users Max", 0)), x[0].lower()))
        if key == "name_asc":
            return sorted(items, key=lambda x: x[0].lower())
        return items

    products_sorted = sort_products(products_filtered, sort_key)

    # Build product cards
    product_cards = []
    for name, pdata in products_sorted:
        keys = pick_summary_keys(pdata)
        # Raw values for on-screen display (templated via |norm)
        summary_pairs = []

        price_map = pdata.get(PRICE_MATRIX_KEY, {})
        price_value = price_map.get(client_category)
        price_label = f"Price ({client_category})"
        if price_value is not None:
            price_display = format_price(price_value)
            summary_pairs.append((price_label, price_display))
        else:
            price_display = None

        summary_pairs.extend((k, str(pdata.get(k, ""))) for k in keys)

        # Normalized pairs for clipboard copy
        summary_pairs_norm = []
        if price_value is not None and price_display is not None:
            summary_pairs_norm.append(
                (
                    _normalize_display_value(price_label),
                    _normalize_display_value(price_display),
                )
            )
        summary_pairs_norm.extend(
            (
                _normalize_display_value(k),
                _normalize_display_value(str(pdata.get(k, ""))),
            )
            for k in keys
        )
        product_cards.append({
            "name": name,
            "description": pdata.get("short_description"),
            "summary": summary_pairs,
            "summary_copy": summary_pairs_norm,
            "pdf_url": pdata.get("pdf_url"),
        })

    # Build selections text for CRM copy
    sel_lines: list[str] = []
    if category:
        sel_lines.append(f"Category: {_normalize_display_value(category)}")
    if client_category:
        sel_lines.append(f"Client Category: {_normalize_display_value(client_category)}")
    for attr in sorted(filters.keys(), key=lambda s: s.lower()):
        vals = ", ".join(_normalize_display_value(v) for v in filters[attr])
        sel_lines.append(f"{_normalize_display_value(attr)}: {vals}")
    if numeric_filters.get("min_router_mbps") is not None:
        sel_lines.append(f"Min Router Throughput: {numeric_filters['min_router_mbps']} Mbps")
    if numeric_filters.get("min_speedfusion_mbps") is not None:
        sel_lines.append(f"Min SpeedFusion: {numeric_filters['min_speedfusion_mbps']} Mbps")
    if numeric_filters.get("min_users") is not None:
        sel_lines.append(f"Min Users: {numeric_filters['min_users']}")
    selections_text = "\n".join(sel_lines)

    return render_template(
        "index.html",
        categories=ALL_CATEGORIES,
        category=category,
        client_categories=CLIENT_CATEGORIES,
        client_category=client_category,
        subcategories=subcategory_values,
        subcategory_attr=SERIES_ATTR,
        quick_picks=quick_picks,
        attributes=attributes,
        filters=filters,
        numeric_filters=numeric_filters,
        sort=sort_key,
        results=product_cards,
        results_count=len(product_cards),
        selections_text=selections_text,
        current_url=request.url,
    )


if __name__ == "__main__":
    # Enable reloader for convenience
    app.run(debug=True, host="127.0.0.1", port=5000, threaded=True)


# --- Operational routes (kept below __main__ guard for readability) ---
@app.get("/health")
def health():
    return {"status": "ok"}
