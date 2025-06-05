import re
from typing import Optional, List, Dict, Tuple


# ------------------------------------------------------------------------
# TABLE & PAGE CROPPING
# ------------------------------------------------------------------------
# Crop box for the transaction table region on each page
TABLE_CROP_BOX: Tuple[float, float, float, float] = (0, 100, 594, 740)

# X‐coordinate ranges for detecting columns
DATE_COLUMN_X0: float = 20.0
DATE_COLUMN_X1: float = 30.0

# X‐coordinate thresholds for splitting code vs. channel vs. debit/credit vs. balance
X_SPLIT_CODE_CHANNEL: float = 80.0
X_SPLIT_CHANNEL_DEBIT_CREDIT: float = 250.0
X_SPLIT_WITHDRAWAL_DEPOSIT: float = 200.0
X_TOLERANCE: float = 2.0    # Fuzzy tolerance when comparing x‐positions

# Y‐margin tolerance for grouping words into rows
Y_MARGIN: float = 3.0

# Footer keywords to skip summary lines
FOOTER_KEYWORDS: List[str] = ["Total amount", "Total items"]

# Region to crop for extracting page ID (e.g. "1/7")
PAGE_ID_CROP_BOX: Tuple[float, float, float, float] = (470, 81, 510, 91)


# ------------------------------------------------------------------------
# REGULAR EXPRESSIONS
# ------------------------------------------------------------------------
MONEY_REGEX = re.compile(r"^[\d,]+\.\d{2}$")      # Matches "1,234.56"
DATE_REGEX  = re.compile(r"^\d{2}/\d{2}/\d{2}$")   # Matches dd/mm/yy
TIME_REGEX  = re.compile(r"^\d{2}:\d{2}$")         # Matches hh:mm

# Cropping boxes (x0, y0, x1, y1) for header fields on each page
CROP_BOXES: Dict[str, Tuple[float, float, float, float]] = {
    "account_name":   (21,  77.1,  80.0,  100.1),
    "account_number": (230, 77.1, 290.1,  87.1),
    "period":         (325, 78.1, 394.4,  88.1),
}

# Header‐footer detection keywords
FOOTER_KEYWORDS_LOWER: List[str] = [kw.lower() for kw in FOOTER_KEYWORDS]