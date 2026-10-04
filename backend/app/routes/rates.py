"""/api/admin/rates — Rates & Rules (billing rates, penalty rule, payment instructions, SOA email).

    GET  /api/admin/rates     current values (the SMTP password is never returned: only smtpPasswordSet)
    PUT  /api/admin/rates     save; send only the fields to change (the React form sends them all)

Permission key `settings` (Superadmin only), the same as the classic /settings screen. Rules are the
classic screen's (legacy.SETTINGS_NUMBERS: decimals, minimum, maximum) plus:
  * every value fits the 255-character settings column (the classic screen could fail on longer text)
  * SMTP port 1–65535, sender email and payment link checked when filled in
  * months as YYYY-MM; "books closed through" may be empty
  * the SMTP password is write-only: a new one replaces it (stored encrypted), smtpPasswordClear removes it
  * due day stays read-only (bills fall due on the 8th; the classic screen did not allow changing it)
Saving changes ONLY the settings rows: no bill, payment or receipt row is written. (Existing billing
behaviour, unchanged: issued bills keep their stored condo dues, parking and water, but the penalty
rule and the storage cut-off month are applied when a statement is computed, so they also affect
unpaid bills, exactly as with the classic screen.) All rows and one audit entry (before/after of what changed, never the password) are
saved in one transaction. A typed-value problem answers 400 with per-field messages and saves nothing.
"""
import re
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..core.numbers import InputError, parse_decimal
from ..utils.auth import json_error, permission_required, protect_api_blueprint

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALUE_MAX = 255  # Setting.value is String(255)

# API field -> setting key. Numbers use the classic screen's label/places/min/max (legacy.SETTINGS_NUMBERS).
NUMBERS = {
    ("ratesPerSqm", "studio"): "studio_rate_per_sqm",
    ("ratesPerSqm", "oneBed"): "one_bed_rate_per_sqm",
    ("ratesPerSqm", "twoBed"): "two_bed_rate_per_sqm",
    ("ratesPerSqm", "threeBed"): "three_bed_rate_per_sqm",
    ("parkingRatePerSqm",): "parking_rate_per_sqm",
    ("storageRatePerSqm",): "storage_rate_per_sqm",
    ("waterRate",): "water_rate",
    ("penaltyRate",): "penalty_rate",
}
TEXTS = {
    "corporationName": ("corporation_name", "Corporation name"),
    "address": ("address", "Address"),
    "onlinePaymentUrl": ("online_payment_url", "Online payment link"),
    "onlinePaymentInstructions": ("online_payment_instructions", "Payment instructions"),
    "smtpHost": ("smtp_host", "SMTP host"),
    "smtpPort": ("smtp_port", "SMTP port"),
    "smtpSender": ("smtp_sender", "Sender email"),
    "smtpUsername": ("smtp_username", "SMTP username"),
}
FLAGS = {
    ("waterAutoCompute",): "water_auto_compute",
    ("penaltyIncludes", "condo"): "penalty_include_condo",
    ("penaltyIncludes", "parking"): "penalty_include_parking",
    ("penaltyIncludes", "storage"): "penalty_include_storage",
    ("penaltyIncludes", "water"): "penalty_include_water",
}
MONTHS = {"storageInTotalFrom": "storage_in_total_from", "booksClosedThrough": "books_closed_through"}

# Same defaults as the classic screen.
DEFAULTS = {
    "corporation_name": "CITYLAND 9 CONDOMINIUM CORPORATION",
    "address": "9 Dela Rosa Condominium, Dela Rosa Street, Barangay Pio del Pilar, Makati City",
    "water_rate": "50", "water_auto_compute": "1",
    "studio_rate_per_sqm": "50", "one_bed_rate_per_sqm": "75", "two_bed_rate_per_sqm": "100", "three_bed_rate_per_sqm": "125",
    "parking_rate_per_sqm": "100", "storage_rate_per_sqm": "50", "storage_in_total_from": "9999-12",
    "penalty_rate": "10",
    "penalty_include_condo": "1", "penalty_include_parking": "0", "penalty_include_storage": "0", "penalty_include_water": "0",
    "online_payment_url": "",
    "online_payment_instructions": "Please use the online payment link or scan the Payment QR shown on this SOA.",
    "smtp_host": "", "smtp_port": "587", "smtp_sender": "", "smtp_username": "",
    "books_closed_through": "",
}


def _path_get(data, path):
    for part in path:
        if not isinstance(data, dict) or part not in data:
            return False, None
        data = data[part]
    return True, data


def _field(path):
    return ".".join(path)


def make_rates_blueprint(legacy):
    bp = protect_api_blueprint(Blueprint("api_rates", __name__, url_prefix="/api/admin/rates"))
    number_rules = {key: (label, places, Decimal(lo), Decimal(hi)) for key, label, places, lo, hi in legacy.SETTINGS_NUMBERS}

    def current(key):
        return legacy.setting(key, DEFAULTS.get(key, ""))

    def payload():
        def nested(mapping, group):
            return {path[1]: (current(key) if mapping is NUMBERS else current(key) == "1")
                    for path, key in mapping.items() if path[0] == group}
        storage_from = current("storage_in_total_from")
        return {
            "corporationName": current("corporation_name"),
            "address": current("address"),
            "ratesPerSqm": nested(NUMBERS, "ratesPerSqm"),
            "parkingRatePerSqm": current("parking_rate_per_sqm"),
            "storageRatePerSqm": current("storage_rate_per_sqm"),
            "waterRate": current("water_rate"),
            "waterAutoCompute": current("water_auto_compute") == "1",
            "penaltyRate": current("penalty_rate"),
            "penaltyIncludes": nested(FLAGS, "penaltyIncludes"),
            # 9999-12 is the classic "never" value: storage is not yet added to SOA totals.
            "storageInTotalFrom": storage_from if MONTH_RE.match(storage_from) else "9999-12",
            "booksClosedThrough": legacy.books_closed_through(),
            "dueDay": legacy.due_day_setting(),
            "onlinePaymentUrl": current("online_payment_url"),
            "onlinePaymentInstructions": current("online_payment_instructions"),
            "smtpHost": current("smtp_host"),
            "smtpPort": current("smtp_port"),
            "smtpSender": current("smtp_sender"),
            "smtpUsername": current("smtp_username"),
            "smtpPasswordSet": bool(legacy.get_smtp_password()),
        }

    @bp.get("")
    @permission_required("settings")
    def get_rates():
        return jsonify({"rates": payload()})

    @bp.put("")
    @permission_required("settings")
    def save_rates():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return json_error(400, "Send the settings as a JSON object.")
        errors, new_values = {}, {}

        for path, key in NUMBERS.items():
            present, raw = _path_get(data, path)
            if not present:
                continue
            label, places, lo, hi = number_rules[key]
            try:
                value = parse_decimal(raw, label=label, places=places, minimum=lo, maximum=hi)
                new_values[key] = format(value.normalize(), "f")
            except InputError as exc:
                errors[_field(path)] = exc.message
        for name, (key, label) in TEXTS.items():
            if name not in data:
                continue
            value = str(data[name] if data[name] is not None else "").strip()
            if len(value) > VALUE_MAX:
                errors[name] = f"{label} can't be longer than {VALUE_MAX} characters."
            elif name == "smtpPort" and value and not (value.isdigit() and 1 <= int(value) <= 65535):
                errors[name] = "SMTP port must be a number from 1 to 65535, e.g. 587."
            elif name == "smtpSender" and value and not EMAIL_RE.match(value):
                errors[name] = "Enter a valid sender email, e.g. billing@example.com."
            elif name == "onlinePaymentUrl" and value and not re.match(r"^https?://\S+$", value):
                errors[name] = "The payment link must start with http:// or https://."
            elif name == "corporationName" and not value:
                errors[name] = "Corporation name is required (it is printed on every SOA)."
            else:
                new_values[key] = value
        for path, key in FLAGS.items():
            present, raw = _path_get(data, path)
            if not present:
                continue
            if not isinstance(raw, bool):
                errors[_field(path)] = "Choose yes or no."
            else:
                new_values[key] = "1" if raw else "0"
        for name, key in MONTHS.items():
            if name not in data:
                continue
            value = str(data[name] or "").strip()
            if name == "booksClosedThrough" and value == "":
                new_values[key] = ""
            elif not MONTH_RE.match(value):
                errors[name] = "Use a month like 2026-10." + (" Leave it empty if the books are not closed." if name == "booksClosedThrough" else "")
            else:
                new_values[key] = value

        smtp_password = data.get("smtpPassword") or ""
        smtp_clear = data.get("smtpPasswordClear") is True
        if not isinstance(smtp_password, str):
            errors["smtpPassword"] = "Enter the password as text."
        elif len(smtp_password) > 200:
            errors["smtpPassword"] = "That password is too long."
        if errors:
            response = jsonify({"error": {"status": 400, "message": next(iter(errors.values())), "fields": errors}})
            response.status_code = 400
            return response

        changes = {}
        for key, value in new_values.items():
            before = current(key)
            if before == value:
                continue
            row = legacy.Setting.query.filter_by(key=key).first() or legacy.Setting(key=key)
            row.value = value
            legacy.db.session.add(row)
            changes[key] = {"before": before, "after": value}
        smtp_note = None
        if smtp_clear and legacy.get_smtp_password():
            legacy.set_smtp_password("")
            smtp_note = "removed"
        elif smtp_password and not smtp_clear:
            legacy.set_smtp_password(smtp_password)
            smtp_note = "replaced"
        if not changes and not smtp_note:
            return jsonify({"rates": payload(), "changed": []})

        if "books_closed_through" in changes:
            c = changes["books_closed_through"]
            legacy.audit(f"Books closed through changed: {c['before'] or 'none'} -> {c['after'] or 'none'}", entity_type="setting",
                         details={"books_closed_through": c}, commit=False)
        details = dict(changes)
        if smtp_note:
            details["smtp_password"] = smtp_note          # never the value
        legacy.audit("Updated Rates & Rules: " + ", ".join(sorted(details)), entity_type="setting",
                     details=details, commit=False)
        legacy.db.session.commit()
        return jsonify({"rates": payload(), "changed": sorted(details)})

    return bp
