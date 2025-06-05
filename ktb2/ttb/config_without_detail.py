import re


# ─────────────────────────────────────────────────────────────────────────────
# Cropping boxes (x0, top, x1, bottom) for each header field
# ─────────────────────────────────────────────────────────────────────────────

CROP_BOUNDS = {
    "account_name": (30, 211.8, 220, 221.8),
    "account_number": (365, 143.7, 450.4, 150.7),
    "period": (365, 163.7, 450, 170.7),
    "owner_branch": (365, 183.7, 500.8, 190.7),
    "total_withdrawal": (500, 227.7, 559.3, 234.7),
    "total_deposit": (500, 247.7, 559.0, 254.7),
    "total_withdrawal_transaction": (380, 227.7, 445, 234.7),
    "total_deposit_transaction": (380, 247.7, 445.0, 254.7),
    "page": (510.4, 42.6, 580.0, 54.6),
}


# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns
# ─────────────────────────────────────────────────────────────────────────────

NUMERIC_REGEX = re.compile(r"[\d,]+(?:\.\d{2})?")
DATE_REGEX = re.compile(r"^\d{1,2}\s[^\s]+\s\d{2}$")
TIME_REGEX = re.compile(r"^\d{2}:\d{2}$")
MONEY_REGEX = re.compile(r"^[\-\+\d,]+\.\d{2}$")
PAGE_ID_REGEX = re.compile(r"(\d+/\d+)")

NUMERIC_FIELDS = {
    "total_withdrawal",
    "total_deposit",
    "total_withdrawal_transaction",
    "total_deposit_transaction",
}


# ─────────────────────────────────────────────────────────────────────────────
# Layout thresholds (x‐coordinates, tolerances, margins)
# ─────────────────────────────────────────────────────────────────────────────

K_DATE_X0 = 20.0
K_DATE_X1 = 80.0
K_X_SPLIT_DESC_AMOUNT = 210.0
K_X_SPLIT_AMOUNT_BALANCE = 420.0
K_X_SPLIT_BALANCE_CHANNEL = 570.0
K_X_SPLIT_CHANNEL_DETAILS = 270.0
K_X_TOLERANCE = 1.0
K_Y_MARGIN = 3.0


# ─────────────────────────────────────────────────────────────────────────────
# Month mappings (Thai ⇄ numeric, English ⇄ numeric)
# ─────────────────────────────────────────────────────────────────────────────

THAI_MONTHS = {
    "ม.ค.": "01", "ก.พ.": "02", "มี.ค.": "03", "เม.ย.": "04",
    "พ.ค.": "05", "มิ.ย.": "06", "ก.ค.": "07", "ส.ค.": "08",
    "ก.ย.": "09", "ต.ค.": "10", "พ.ย.": "11", "ธ.ค.": "12"
}

ENG_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"
}
