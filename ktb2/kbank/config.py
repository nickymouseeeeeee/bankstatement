# ------------------------------------------------------------------------
# Crop regions for header fields (x0, y0, x1, y1)
# ------------------------------------------------------------------------
CROP_REGIONS = {
    "account_name": (70, 110.1, 220.9, 120.1),
    "address": (53, 130.1, 220.9, 140.1),
    "reference_code": (406, 101.1, 550.4, 111.1),
    "account_number": (416, 116.1, 480.4, 126.1),
    "period": (406, 131.1, 520.2, 141.1),
    "owner_branch": (406, 147.1, 520.8, 157.1),
    "ending_balance": (420, 165.1, 570.9, 175.1),
    "total_withdrawal": (500, 180.1, 570.3, 190.1),
    "total_deposit": (500, 195.1, 570.3, 205.1),
    "total_withdrawal_transaction": (320, 180.1, 469.3, 190.1),
    "total_deposit_transaction": (320, 195.1, 469.3, 205.1),
    "page": (240.4, 99.1, 310.0, 109.1)
}


# ------------------------------------------------------------------------
# Column split boundaries
# ------------------------------------------------------------------------
X_BOUNDS = {
    "date_min": 40.0,
    "date_max": 80.0,
    "desc_start": 100.0,
    "amount_desc_split": 280.0,
    "withdraw_deposit_split": 240.0,
    "withdraw_deposit_split_x1": 250.0,
    "amount_balance_split": 300.0,
    "balance_channel_split": 460.0,
    "channel_details_split": 420.0
}


# ------------------------------------------------------------------------
# Tolerances and margins
# ------------------------------------------------------------------------
tolerance_settings = {
    "x_tolerance": 2.0,
    "y_margin": 2.0
}


# ------------------------------------------------------------------------
# PDF table detection defaults
# ------------------------------------------------------------------------
TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 1
}


# ------------------------------------------------------------------------
# Regex patterns
# ------------------------------------------------------------------------
import re

DATE_PATTERN = re.compile(r"^\d{2}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")
MONEY_PATTERN = re.compile(r"^[\d,]+\.\d{2}$")
PAGE_ID_PATTERN = re.compile(r"(\d+/\d+)")
NUMERIC_CLEAN_PATTERN = re.compile(r"[\d,]+(?:\.\d{2})?")
