import hashlib

from markupsafe import Markup

HUES = ("rose", "amber", "lime", "teal", "sky", "indigo", "violet", "magenta",
        "brown", "grey")

DERIVED_HUES = HUES[:-1]

DEFAULT_HUE = "grey"


def normalize_hue(value):
    value = (value or "").strip().lower()
    return value if value in HUES else ""


def _stable_index(text, modulus):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % modulus


def colour_for(name, stored=""):
    chosen = normalize_hue(stored)
    if chosen:
        return chosen
    key = (name or "").strip().casefold()
    if not key:
        return DEFAULT_HUE
    return DERIVED_HUES[_stable_index(key, len(DERIVED_HUES))]


ICONS = {
    "tag": '<path d="M3 11.5V4a1 1 0 0 1 1-1h7.5L21 12.5 12.5 21 3 11.5Z"/>'
           '<circle cx="7.5" cy="7.5" r="1.3"/>',
    "cart": '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/>'
            '<path d="M2 3h2.5l2.6 11.2a2 2 0 0 0 2 1.6h8.4a2 2 0 0 0 2-1.5'
            'L21 8H6"/>',
    "cup": '<path d="M4 8h12v6a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V8Z"/>'
           '<path d="M16 10h2.5a2.5 2.5 0 0 1 0 4.5H16"/>',
    "fork": '<path d="M6 2v7a2 2 0 0 0 4 0V2"/><path d="M8 11v11"/>'
            '<path d="M16 22V9a3 3 0 0 1 0-7"/>',
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>'
            '<path d="M10 21v-6h4v6"/>',
    "car": '<path d="M4 16v-3.5L6 7h12l2 5.5V16"/><path d="M4 16h16"/>'
           '<circle cx="7.5" cy="17.5" r="2"/>'
           '<circle cx="16.5" cy="17.5" r="2"/>',
    "bolt": '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>',
    "wifi": '<path d="M3 9a15 15 0 0 1 18 0"/>'
            '<path d="M6.5 12.5a10 10 0 0 1 11 0"/>'
            '<path d="M10 16a5 5 0 0 1 4 0"/><circle cx="12" cy="19.5" r="1"/>',
    "heart": '<path d="M12 20s-7-4.5-7-9.5A4 4 0 0 1 12 7a4 4 0 0 1 7 3.5'
             'c0 5-7 9.5-7 9.5Z"/>',
    "bag": '<path d="M5 8h14l-1 13H6L5 8Z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
    "film": '<rect x="3" y="4" width="18" height="16" rx="2"/>'
            '<path d="M3 9h18M3 15h18M8 4v16M16 4v16"/>',
    "plane": '<path d="M2 13l20-8-8 20-2.5-7.5L2 13Z"/>',
    "book": '<path d="M4 4h7a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4V4Z"/>'
            '<path d="M20 4h-7a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h7V4Z"/>',
    "gift": '<rect x="3" y="8" width="18" height="13" rx="1.5"/>'
            '<path d="M3 13h18M12 8v13"/>'
            '<path d="M12 8S10 3 7.5 4.5 9 8 12 8Z"/>'
            '<path d="M12 8s2-5 4.5-3.5S15 8 12 8Z"/>',
    "paw": '<circle cx="7" cy="9" r="2"/><circle cx="12" cy="7" r="2"/>'
           '<circle cx="17" cy="9" r="2"/>'
           '<path d="M12 12c3 0 5 2 5 4.5S14.5 21 12 21s-5-2-5-4.5S9 12 12 12Z"/>',
    "dumbbell": '<path d="M4 9v6M7 7v10M17 7v10M20 9v6M7 12h10"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3Z"/>',
    "bank": '<path d="M3 10 12 4l9 6"/><path d="M5 10v9M19 10v9M3 20h18"/>'
            '<path d="M9 20v-6M15 20v-6"/>',
    "coins": '<ellipse cx="12" cy="6" rx="8" ry="3"/>'
             '<path d="M4 6v5c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/>'
             '<path d="M4 11v5c0 1.7 3.6 3 8 3s8-1.3 8-3v-5"/>',
    "card": '<rect x="2" y="5" width="20" height="14" rx="2"/>'
            '<path d="M2 10h20"/><path d="M6 15h4"/>',
    "chart": '<path d="M3 21h18"/><path d="M6 21V11M11 21V5M16 21v-7M21 21V8"/>',
    "wallet": '<path d="M3 7a2 2 0 0 1 2-2h12v4"/>'
              '<rect x="3" y="7" width="18" height="12" rx="2"/>'
              '<path d="M16 13h5"/>',
    "receipt": '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2V3Z"/>'
               '<path d="M9 8h6M9 12h6"/>',
}

DEFAULT_ICON = "tag"

_ICON_WORDS = (
    (("insurance", "premium"), "shield"),
    (("internet", "broadband", "phone", "mobile", "wifi", "cable"), "wifi"),
    (("grocer", "supermarket", "food shop", "household"), "cart"),
    (("coffee", "cafe", "tea"), "cup"),
    (("restaurant", "dining", "eating out", "takeaway", "takeout",
      "lunch", "dinner", "food"), "fork"),
    (("rent", "mortgage", "housing", "house", "home"), "home"),
    (("fuel", "petrol", "gas station", "car", "auto", "transport",
      "transit", "parking", "train", "bus"), "car"),
    (("electric", "power", "utility", "utilities", "energy", "water",
      "heating"), "bolt"),
    (("health", "medical", "doctor", "dental", "dentist", "pharmacy",
      "prescription"), "heart"),
    (("clothes", "clothing", "shopping", "apparel", "shoes"), "bag"),
    (("entertainment", "subscription", "streaming", "cinema",
      "movie", "music", "game"), "film"),
    (("travel", "holiday", "vacation", "flight", "hotel"), "plane"),
    (("education", "school", "tuition", "course", "book"), "book"),
    (("gift", "present", "charity", "donation", "birthday"), "gift"),
    (("pet", "dog", "vet"), "paw"),
    (("gym", "fitness", "sport", "exercise"), "dumbbell"),
    (("bank", "loan", "fee", "interest", "charge"), "bank"),
    (("salary", "wage", "paycheck", "payslip", "income", "bonus", "dividend",
      "refund"), "coins"),
    (("saving", "invest", "pension", "retirement", "portfolio"), "chart"),
    (("credit card", "card"), "card"),
    (("cash", "withdrawal", "atm", "allowance", "pocket money"), "wallet"),
    (("bill", "tax", "licence", "license"), "receipt"),
)

ACCOUNT_KIND_ICONS = {
    "checking": "wallet",
    "savings": "coins",
    "cash": "wallet",
    "credit": "card",
    "loan": "bank",
    "investment": "chart",
    "property": "home",
    "vehicle": "car",
    "other": "tag",
}


def normalize_icon(value):
    value = (value or "").strip().lower()
    return value if value in ICONS else ""


def icon_for(name, stored="", kind=""):
    chosen = normalize_icon(stored)
    if chosen:
        return chosen
    from_kind = ACCOUNT_KIND_ICONS.get((kind or "").strip().lower())
    if from_kind:
        return from_kind
    key = (name or "").strip().casefold()
    for fragments, icon in _ICON_WORDS:
        if any(fragment in key for fragment in fragments):
            return icon
    return DEFAULT_ICON


def markup_for(icon):
    return ICONS.get(normalize_icon(icon) or DEFAULT_ICON, ICONS[DEFAULT_ICON])


_FRAME = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
          '{inner}</svg>')


def chip(icon, colour, size="", flat=False, label=""):
    classes = ["gl"]
    if size in ("sm", "lg"):
        classes.append(size)
    if flat:
        classes.append("flat")
    classes.append("h-" + (normalize_hue(colour) or DEFAULT_HUE))
    attrs = ('role="img" aria-label="{}"'.format(Markup.escape(label))
             if label else 'aria-hidden="true"')
    return Markup(
        '<span class="{klass}" {attrs}>' + _FRAME + '</span>'
    ).format(klass=" ".join(classes), attrs=Markup(attrs),
             inner=Markup(markup_for(icon)))


def decorate(row, kind_field="kind"):
    out = dict(row)
    out["colour"] = colour_for(out.get("name"), out.get("colour", ""))
    out["icon"] = icon_for(out.get("name"), out.get("icon", ""),
                           out.get(kind_field, "") if kind_field else "")
    return out
