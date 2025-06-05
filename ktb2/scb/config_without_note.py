# ------------------------------------------------------------------------
# File: config.py
# ------------------------------------------------------------------------
import re
from typing import Optional, Dict, Tuple

# ------------------------------------------------------------------------
# X‐position ranges to locate date tokens (table row start) in PDF coordinates
# ------------------------------------------------------------------------
DATE_X0: float = 20.0
DATE_X1: float = 30.0


# ------------------------------------------------------------------------
# Splitting X‐coordinates for different table columns
# ------------------------------------------------------------------------
X_SPLIT_CODE_CHANNEL: float = 120.0
X_SPLIT_CHANNEL_DEBIT_CREDIT: float = 300.0
X_SPLIT_DEBIT_CREDIT_BALANCE: float = 400.0
X_SPLIT_BALANCE_DESCRIPTION: float = 550.0
X_SPLIT_WITHDRAWAL_DEPOSIT: float = 280.0


# ------------------------------------------------------------------------
# Tolerance and margins for robust coordinate matching
# ------------------------------------------------------------------------
X_TOLERANCE: float = 2.0
Y_MARGIN: float = 3.0


# ------------------------------------------------------------------------
# Table detection settings for pdfplumber
# ------------------------------------------------------------------------
TABLE_SETTINGS: Dict[str, object] = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 1
}


# ------------------------------------------------------------------------
# Regular expressions
# ------------------------------------------------------------------------
MONEY_PATTERN      = re.compile(r"^[\d,]+\.\d{2}$")            # Detects monetary fields (e.g., 1,234.56)
DATE_PATTERN       = re.compile(r"^\d{2}/\d{2}/\d{2}$")        # Detects dates like 01/02/21
TIME_PATTERN       = re.compile(r"^\d{2}:\d{2}$")              # Detects times like 14:35
PAGE_ID_PATTERN    = re.compile(r"\b(\d+)\s*of\s*(\d+)\b", re.IGNORECASE)


# ------------------------------------------------------------------------
# Bounding boxes (x0, top, x1, bottom) for cropping header fields
# ------------------------------------------------------------------------
HEADER_CROP_REGIONS: Dict[str, Tuple[float, float, float, float]] = {
    "account_name":                        (70,   95.9, 230.4, 105.9),
    "address":                             (70,  141.9, 260.3, 180.9),
    "account_number":                      (415,  95.9, 550.3, 100.9),
    "period":                              (398, 151.9, 586.8, 156.9),
    "owner_branch":                        (30,   66.9, 180.6,  81.9),
    "total_withdrawal_summary":            (203, 742.0, 301.1, 755.6),
    "total_deposit_summary":               (203, 760.0, 301.1, 775.6),
    "total_withdrawal_transaction_summary":(160, 780.0, 280.1, 795.6),
    "total_deposit_transaction_summary":   (265.2,780.0, 301.1, 795.6)
}
