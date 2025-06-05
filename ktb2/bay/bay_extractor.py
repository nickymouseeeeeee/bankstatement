import re
import pdfplumber
import pandas as pd
from typing import Optional, Dict, Tuple, List

import config


class BAYStatementExtractor:
    """
    Encapsulates extraction of headers and transactions from a BAY bank‑statement PDF.
    All helper methods are static; only `run()` remains an instance method.
    """

    # Compile regexes at the class level
    DATE_REGEX = re.compile(config.DATE_PATTERN)
    TIME_REGEX = re.compile(config.TIME_PATTERN)
    MONEY_REGEX = re.compile(config.MONEY_PATTERN)

    @staticmethod
    def clean_float_column(series: pd.Series) -> pd.Series:
        """
        Strip non‑numeric characters from a column of strings, and convert to float.
        """
        cleaned = series.astype(str).apply(lambda s: re.sub(r"[^0-9\.]", "", s))
        return pd.to_numeric(cleaned, errors="coerce")

    @staticmethod
    def clean_page_id(raw_page_id: str) -> str:
        """
        Standardize a raw page‑id string into "N/M" format.
        """
        numeric_parts = re.findall(r"\d+", raw_page_id)
        if len(numeric_parts) >= 2:
            candidate = f"{numeric_parts[0]}/{numeric_parts[1]}"
            if re.fullmatch(r"\d+/\d+", candidate):
                return candidate
        return ""

    @staticmethod
    def extract_page_id(page: pdfplumber.page.Page) -> str:
        """
        Try multiple crop‑regions until we successfully read a page‑id in "N/M" format.
        """
        for bbox in config.PAGE_ID_CROPS:
            try:
                raw_text = page.crop(bbox).extract_text() or ""
                page_id_candidate = BAYStatementExtractor.clean_page_id(raw_text.strip())
                if page_id_candidate:
                    return page_id_candidate
            except Exception:
                continue

        return ""

    @staticmethod
    def extract_headers(
        pages: List[pdfplumber.page.Page]
    ) -> List[Dict[str, Optional[str]]]:
        """
        Loop over a list of `pdfplumber.page.Page` objects and build a list of header‑dicts:
          - page_id
          - account_name
          - account_number
          - period
          - total_withdrawal_transaction, total_withdrawal
          - total_deposit_transaction, total_deposit
        """
        header_records: List[Dict[str, Optional[str]]] = []

        for page in pages:
            try:
                page_id = BAYStatementExtractor.extract_page_id(page)
                full_text = page.extract_text() or ""
                footer_present = any(
                    kw.lower() in full_text.lower()
                    for kw in config.FOOTER_KEYWORDS_HEADER
                )
                has_header_info = any(
                    kw.lower() in full_text.lower()
                    for kw in ["ชื่อบัญชี", "Account No."]
                )

                header_record: Dict[str, Optional[str]] = {"page_id": page_id}

                # Extract each field from its bbox, if header keywords exist
                for field_name, bbox in config.HEADER_CROPS.items():
                    try:
                        if has_header_info:
                            raw_field_text = page.crop(bbox).extract_text() or ""
                            header_record[field_name] = raw_field_text.strip().replace("\n", " ")
                        else:
                            header_record[field_name] = None
                    except Exception:
                        header_record[field_name] = None

                # If footer is present, parse the totals lines
                if footer_present:
                    for line in full_text.splitlines():
                        if line.startswith(("Total Withdrawal", "รายการถอนเงิน")):
                            numbers = re.findall(r"[\d,]+(?:\.\d{2})?", line)
                            header_record["total_withdrawal_transaction"] = (
                                numbers[0].replace(",", "") if len(numbers) > 0 else None
                            )
                            header_record["total_withdrawal"] = (
                                numbers[1].replace(",", "") if len(numbers) > 1 else None
                            )

                        elif line.startswith(("Total Deposit", "รายการฝากเงิน")):
                            numbers = re.findall(r"[\d,]+(?:\.\d{2})?", line)
                            header_record["total_deposit_transaction"] = (
                                numbers[0].replace(",", "") if len(numbers) > 0 else None
                            )
                            header_record["total_deposit"] = (
                                numbers[1].replace(",", "") if len(numbers) > 1 else None
                            )

                header_records.append(header_record)

            except Exception:
                # Skip this page if anything goes wrong
                continue

        # After collecting all page‑headers, post‑process “period” into datetimes
        header_df = pd.DataFrame(header_records)
        if "period" in header_df.columns:
            working_copy = header_df.copy()
            period_series = working_copy["period"].fillna("").str.replace(" ", "")
            split_period = period_series.str.split(r"[-–]", n=1, expand=True)

            if split_period.shape[1] < 2:
                split_period[1] = None

            working_copy["start_period"] = pd.to_datetime(
                split_period[0], dayfirst=True, errors="coerce"
            )
            working_copy["end_period"] = pd.to_datetime(
                split_period[1], dayfirst=True, errors="coerce"
            )
            header_df = working_copy

        return header_df.to_dict(orient="records")

    @staticmethod
    def extract_transactions(
        pages: List[pdfplumber.page.Page]
    ) -> List[Dict[str, Optional[str or float]]]:
        """
        Loop over each page, find table regions, group words by row, and build
        a list of transaction‑dicts with keys:
          page_id, date, time, code, channel, withdrawal, deposit, balance, description
        """
        transaction_records: List[Dict[str, Optional[str or float]]] = []

        for page in pages:
            try:
                page_id = BAYStatementExtractor.extract_page_id(page)

                # Find tables via pdfplumber (if any)
                try:
                    tables_on_page = page.find_tables(config.TABLE_SETTINGS)
                except Exception:
                    tables_on_page = []

                if tables_on_page:
                    regions = []
                    for table_obj in tables_on_page:
                        try:
                            regions.append(page.crop(table_obj.bbox))
                        except Exception:
                            pass
                else:
                    regions = [page]

                for region in regions:
                    try:
                        words = region.extract_words(use_text_flow=True)
                    except Exception:
                        continue

                    # Bucket words into rows by vertical position
                    row_buckets: Dict[int, List[dict]] = {}
                    for word in words:
                        row_key = int(word["top"] // config.Y_MARGIN)
                        row_buckets.setdefault(row_key, []).append(word)

                    # Check for any footer lines in those rows
                    footer_y_positions = [
                        min(w["top"] for w in one_row)
                        for one_row in row_buckets.values()
                        if any(
                            kw in " ".join(w["text"] for w in one_row)
                            for kw in config.TABLE_FOOTER_KEYWORDS
                        )
                    ]

                    if footer_y_positions:
                        cutoff = min(footer_y_positions) - config.TABLE_FOOTER_MARGIN
                        full_height = region.bbox[3] - region.bbox[1]
                        if 0 < cutoff < full_height:
                            try:
                                cropped_region = region.crop(
                                    (0, 0, region.width, cutoff), relative=True
                                )
                                words = cropped_region.extract_words(use_text_flow=True)
                            except Exception:
                                pass

                            # Re‑bucket after cropping
                            row_buckets.clear()
                            for word in words:
                                row_key = int(word["top"] // config.Y_MARGIN)
                                row_buckets.setdefault(row_key, []).append(word)

                    # Build “interval” y‑ranges from any date‑like words
                    sorted_tops = sorted(
                        w["top"]
                        for w in words
                        if BAYStatementExtractor.DATE_REGEX.match(w["text"])
                        and config.DATE_COLUMN_X0 <= w["x0"] <= config.DATE_COLUMN_X1
                    )
                    if not sorted_tops:
                        continue

                    intervals: List[Tuple[float, float]] = []
                    for idx_top, y_val in enumerate(sorted_tops):
                        start_y = y_val - config.Y_MARGIN
                        if idx_top + 1 < len(sorted_tops):
                            next_y = sorted_tops[idx_top + 1]
                            end_y = next_y - config.Y_MARGIN
                        else:
                            previous_y = (
                                sorted_tops[idx_top - 1]
                                if idx_top > 0
                                else (y_val - 2 * config.Y_MARGIN)
                            )
                            end_y = y_val + (y_val - previous_y) - config.Y_MARGIN
                        intervals.append((start_y, end_y))

                    # Assign words to each interval (row)
                    rows_of_words: List[List[dict]] = [[] for _ in intervals]
                    for word in words:
                        for interval_index, (start_y, end_y) in enumerate(intervals):
                            if start_y <= word["top"] < end_y:
                                rows_of_words[interval_index].append(word)
                                break

                    # Parse each “row” into structured fields
                    for row_words in rows_of_words:
                        if not row_words:
                            continue

                        row_words_sorted = sorted(
                            row_words, key=lambda w: (w["top"], w["x0"])
                        )
                        row_text_combined = " ".join(w["text"] for w in row_words_sorted)

                        # Skip total lines
                        if any(lbl in row_text_combined for lbl in ("TOTAL AMOUNTS", "TOTAL ITEMS")):
                            continue

                        date_text = next(
                            (w["text"] for w in row_words_sorted if BAYStatementExtractor.DATE_REGEX.match(w["text"])),
                            ""
                        )
                        time_text = next(
                            (w["text"] for w in row_words_sorted if BAYStatementExtractor.TIME_REGEX.match(w["text"])),
                            ""
                        )

                        code_parts: List[str] = []
                        channel_parts: List[str] = []
                        description_parts: List[str] = []
                        dc_word_candidates: List[dict] = []
                        balance_word_candidates: List[dict] = []

                        for w in row_words_sorted:
                            text_token = w["text"]
                            x0 = w["x0"]

                            if BAYStatementExtractor.DATE_REGEX.match(text_token) or BAYStatementExtractor.TIME_REGEX.match(text_token):
                                continue

                            if BAYStatementExtractor.MONEY_REGEX.match(text_token):
                                # Is it debit/credit or balance?
                                if config.CHANNEL_DC_SPLIT_X <= x0 <= config.DC_BALANCE_SPLIT_X:
                                    dc_word_candidates.append(w)
                                elif config.DC_BALANCE_SPLIT_X <= x0 <= config.BALANCE_DESCRIPTION_SPLIT_X:
                                    balance_word_candidates.append(w)
                                continue

                            if x0 <= config.CODE_CHANNEL_SPLIT_X + config.X_TOLERANCE:
                                code_parts.append(text_token)
                            elif x0 <= config.CHANNEL_SPLIT_X + config.X_TOLERANCE:
                                channel_parts.append(text_token)
                            else:
                                description_parts.append(text_token)

                        withdrawal_value: Optional[float] = None
                        deposit_value: Optional[float] = None
                        for candidate in dc_word_candidates:
                            try:
                                numeric_val = float(candidate["text"].replace(",", ""))
                            except Exception:
                                numeric_val = None

                            if numeric_val is not None:
                                if candidate["x1"] <= config.WITHDRAW_DEPOSIT_SPLIT_X:
                                    withdrawal_value = numeric_val
                                else:
                                    deposit_value = numeric_val

                        balance_value: Optional[float] = None
                        if balance_word_candidates:
                            chosen = next(
                                (w for w in balance_word_candidates if w["x0"] >= config.DC_BALANCE_SPLIT_X),
                                None
                            )
                            if chosen:
                                try:
                                    balance_value = float(chosen["text"].replace(",", ""))
                                except Exception:
                                    balance_value = None

                        transaction_records.append({
                            "page_id":    page_id,
                            "date":       date_text,
                            "time":       time_text,
                            "code":       " ".join(code_parts),
                            "channel":    " ".join(channel_parts),
                            "withdrawal": withdrawal_value,
                            "deposit":    deposit_value,
                            "balance":    balance_value,
                            "description": " ".join(description_parts),
                        })

            except Exception:
                continue

        return transaction_records

    @staticmethod
    def clean_extracted_data(
        header_dataframe: pd.DataFrame,
        transaction_dataframe: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Standardize column names, rename withdrawal→debit, deposit→credit,
        drop unused columns, fill NaNs, and cast to float where needed.
        """
        # Work on copies to avoid assignment‑on‑slice issues:
        header_copy = header_dataframe.copy()
        transaction_copy = transaction_dataframe.copy()

        # Select and rename in header: total_withdrawal→total_debit, etc.
        header_selected = header_copy[[
            "page_id", "account_name", "account_number", "period",
            "total_withdrawal", "total_deposit",
            "total_withdrawal_transaction", "total_deposit_transaction"
        ]].copy()

        header_selected = header_selected.rename(columns={
            "total_withdrawal":             "total_debit",
            "total_deposit":                "total_credit",
            "total_withdrawal_transaction": "total_debit_transaction",
            "total_deposit_transaction":    "total_credit_transaction"
        })

        # Rename in transactions: withdrawal→debit, deposit→credit
        transaction_selected = transaction_copy.copy()
        transaction_selected = transaction_selected.rename(columns={
            "withdrawal": "debit",
            "deposit":    "credit"
        })

        # Create transaction_type column from “code”, then drop “code”
        transaction_selected["transaction_type"] = transaction_selected["code"]
        transaction_selected["code"] = None

        # Clean page_id strings in both DataFrames
        header_selected["page_id"] = header_selected["page_id"].apply(BAYStatementExtractor.clean_page_id)
        transaction_selected["page_id"] = transaction_selected["page_id"].apply(BAYStatementExtractor.clean_page_id)

        # Fill missing with empty strings
        header_selected.fillna("", inplace=True)
        transaction_selected.fillna("", inplace=True)

        # Add empty “address” column
        header_selected["address"] = ""

        # Cast numeric columns to floats
        for col_name in ["debit", "credit", "balance"]:
            if col_name in transaction_selected.columns:
                transaction_selected[col_name] = BAYStatementExtractor.clean_float_column(transaction_selected[col_name])

        for col_name in [
            "total_debit", "total_credit",
            "total_debit_transaction", "total_credit_transaction"
        ]:
            if col_name in header_selected.columns:
                header_selected[col_name] = BAYStatementExtractor.clean_float_column(header_selected[col_name])

        return header_selected, transaction_selected

    @staticmethod
    def run(pdf_path: str, password: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Open the PDF, extract headers & transactions, clean them, and return two DataFrames.
        This is now a static method because it does not rely on any instance state.
        """
        with pdfplumber.open(pdf_path, password=password) as pdf:
            pages = pdf.pages
            # call other static helpers via the class name (or directly)
            raw_header_records      = BAYStatementExtractor.extract_headers(pages)
            raw_transaction_records = BAYStatementExtractor.extract_transactions(pages)

        header_df      = pd.DataFrame(raw_header_records)
        transaction_df = pd.DataFrame(raw_transaction_records)
        cleaned_header_df, cleaned_transaction_df = BAYStatementExtractor.clean_extracted_data(
            header_df, transaction_df
        )
        return cleaned_header_df, cleaned_transaction_df