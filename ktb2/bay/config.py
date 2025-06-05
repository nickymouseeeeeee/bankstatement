# config.py

import re  # Required to compile and use regular expressions for pattern matching
#from typing import Optional, Tuple, List, Dict

# ------------------------------------------------------------------------------
# Page-ID Cropping Boxes (try multiple to guard against minor layout shifts)
# ------------------------------------------------------------------------------
PAGE_ID_CROPS: list[tuple[float, float, float, float]] = [
    (550.7, 23.6, 600.0, 40.3),
    (550.7, 23.6, 594.0, 40.3),
]

# ------------------------------------------------------------------------------
# Header Fields: bounding boxes for account_name, account_number, period
# ------------------------------------------------------------------------------
HEADER_CROPS: dict[str, tuple[float, float, float, float]] = {
    "account_name":   (370.0, 125.9, 500.2, 132.9),
    "account_number": (384.0, 137.9, 438.5, 144.9),
    "period":         (384.0, 161.9, 500.4, 168.9),
}

# ------------------------------------------------------------------------------
# Footer Keywords (English / Thai) to detect total‐withdrawal / total‐deposit lines
# ------------------------------------------------------------------------------
FOOTER_KEYWORDS_HEADER: list[str] = [
    "Total Withdrawal", "Total Deposit",
    "รายการถอนเงิน", "รายการฝากเงิน"
]

# ------------------------------------------------------------------------------
# pdfplumber Table‐Finding Settings (vertical/horizontal strategy, tolerance)
# ------------------------------------------------------------------------------
TABLE_SETTINGS: dict[str, str | int] = {
    "vertical_strategy":    "lines",
    "horizontal_strategy":  "lines",
    "intersection_tolerance": 1,
}

# ------------------------------------------------------------------------------
# Regex Patterns for Date, Time, Money (strings only; compilation happens in main code)
# ------------------------------------------------------------------------------
DATE_PATTERN: str = r"^\d{2}/\d{2}/\d{4}$"
TIME_PATTERN: str = r"^\d{2}:\d{2}:\d{2}$"
MONEY_PATTERN: str = r"^[\d,]+\.\d{2}$"

# ------------------------------------------------------------------------------
# X-coordinate splits for columns (all values in PDF points)
# ------------------------------------------------------------------------------
DATE_COLUMN_X0: float = 1.0
DATE_COLUMN_X1: float = 30.0

CODE_CHANNEL_SPLIT_X: float = 120.0
CHANNEL_SPLIT_X: float = 450.0
CHANNEL_DC_SPLIT_X: float = 200.0
WITHDRAW_DEPOSIT_SPLIT_X: float = 278.0
DC_BALANCE_SPLIT_X: float = 320.0
BALANCE_DESCRIPTION_SPLIT_X: float = 400.0

# ------------------------------------------------------------------------------
# Tolerances and Margins
# ------------------------------------------------------------------------------
X_TOLERANCE: float = 1.0
Y_MARGIN: float = 3.0

# ------------------------------------------------------------------------------
# Footer Keywords for transaction table (so we can crop off the bottom totals)
# ------------------------------------------------------------------------------
TABLE_FOOTER_KEYWORDS: list[str] = ["รายการถอนเงิน", "Total Withdrawal"]
TABLE_FOOTER_MARGIN: float = 3.0
