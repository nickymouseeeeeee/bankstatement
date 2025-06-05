# extractor.py

import re
import pdfplumber
import pandas as pd
from typing import Optional, List, Dict, Tuple

import config  # assumes config.py is in the same folder


class TTBdetailExtractor:
    """
    Class to extract TTB (Thai Trust Bank) statement data from a PDF,
    in three stages:
      1) extract_headers    → raw_headers_df
      2) extract_transactions → raw_transactions_df
      3) clean_dataframes(raw_headers_df, raw_transactions_df)
         → (clean_header_df, clean_transaction_df)
    """
    @staticmethod
    def run(
        pdf_path: str,
        password: Optional[str]
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Open the PDF at pdf_path (with given password), and invoke the three‐stage pipeline:
          • extract_headers
          • extract_transactions
          • clean_dataframes
        Returns (clean_header_df, clean_transaction_df).
        """
        with pdfplumber.open(pdf_path, password=password) as pdf:
            pages = pdf.pages

            raw_headers_df = TTBdetailExtractor.extract_headers(pages)
            raw_transactions_df = TTBdetailExtractor.extract_transactions(pages)
            clean_header_df, clean_transaction_df = TTBdetailExtractor.clean_dataframes(
                raw_headers_df, raw_transactions_df
            )

        return clean_header_df, clean_transaction_df

    # -------------------------------------------------------------------------
    # 1) RAW HEADER EXTRACTION
    # -------------------------------------------------------------------------
    @staticmethod
    def extract_headers(pages: List[pdfplumber.pdf.Page]) -> pd.DataFrame:
        """
        Iterate through all pages, crop each header field, parse numeric fields,
        and collect a list of header‐dicts. Returns a DataFrame with columns:
          [all original crop keys plus “page_id”].
        """
        header_records: List[Dict[str, Optional[str]]] = []

        for page_number, page in enumerate(pages, start=1):
            try:
                header_dict: Dict[str, Optional[str]] = {}
                # 1a) Extract every cropped field
                for field_name, bbox in config.CROPS.items():
                    try:
                        txt = page.crop(bbox).extract_text() or ""
                    except Exception:
                        txt = ""
                    raw_text = txt.strip()

                    # If this field is in NUMERIC_FIELDS, extract only digits (e.g. “1,234.56” → “1234.56”)
                    if field_name in config.NUMERIC_FIELDS:
                        m = config.NUMERIC_REGEX.search(raw_text)
                        header_dict[field_name] = m.group().replace(",", "") if m else None
                    else:
                        header_dict[field_name] = raw_text

                # 1b) Derive “page_id” from the cropped “page” field
                page_text = header_dict.get("page", "") or ""
                m_id = config.PAGE_ID_REGEX.search(page_text)
                page_id_value = m_id.group(1) if m_id else ""
                header_dict["page_id"] = page_id_value

                # 1c) If not the first‐page header (i.e. “1/…”), null out everything except page_id
                if not page_id_value.startswith("1/"):
                    for key in config.CROPS.keys():
                        if key != "page_id":
                            header_dict[key] = None

                header_records.append(header_dict)

            except Exception as err:
                print(f"[!] Skipped header on page {page_number} due to: {err}")
                continue

        # Convert list of dicts into DataFrame (and .copy() immediately)
        if header_records:
            raw_header_df = pd.DataFrame(header_records).copy()
        else:
            cols = list(config.CROPS.keys()) + ["page_id"]
            raw_header_df = pd.DataFrame(columns=cols).copy()

        return raw_header_df

    # -------------------------------------------------------------------------
    # 2) RAW TRANSACTION EXTRACTION
    # -------------------------------------------------------------------------
    @staticmethod
    def extract_transactions(pages: List[pdfplumber.pdf.Page]) -> pd.DataFrame:
        """
        Iterate through all pages, find “date‐top” positions, group words into rows,
        parse date/time/amounts/description/channel/transaction_type, and return a DataFrame.
        Columns (raw): 
          ['page_id','date','time','description','withdrawal','deposit','balance','channel','transaction_type']
        """
        transaction_records: List[Dict[str, Optional[str]]] = []

        for page_number, page in enumerate(pages, start=1):
            try:
                all_words = page.extract_words(use_text_flow=True)
                # Re‐extract page_id from header crop
                header_crop_text = page.crop(config.CROPS["page"]).extract_text() or ""
                m_id = config.PAGE_ID_REGEX.search(header_crop_text.strip())
                page_id_value = m_id.group(1) if m_id else ""

                # 2a) Find vertical “tops” where a date token trio appears
                date_tops = TTBdetailExtractor.compute_date_tops(all_words)
                if not date_tops:
                    continue  # no “transactions” on this page

                # 2b) Create vertical intervals between each date‐top
                intervals = TTBdetailExtractor.compute_intervals(date_tops)

                # 2c) Assign every word to exactly one row‐bucket
                row_groups = TTBdetailExtractor.assign_to_rows(all_words, intervals)

                # 2d) For each row, interpret date/time/amount fields
                for row in row_groups:
                    if not row:
                        continue

                    sorted_row = sorted(row, key=lambda w: (w["top"], w["x0"]))
                    date_value = ""
                    time_value = ""
                    debit_value = None
                    credit_value = None
                    balance_value = None
                    channel_tokens: List[str] = []
                    transaction_type_tokens: List[str] = []
                    detail_tokens: List[str] = []

                    for word in sorted_row:
                        x0 = word["x0"]
                        text = word["text"]

                        # a) DATE_TOKEN
                        if config.DATE_REGEX.match(text) and (config.K_DATE_X0 <= x0 <= config.K_DATE_X1):
                            date_value = text

                        # b) TIME_TOKEN
                        elif config.TIME_REGEX.match(text):
                            time_value = text

                        # c) MONEY_TOKEN
                        elif config.MONEY_REGEX.match(text):
                            numeric_amount = float(text.replace(",", ""))
                            if x0 <= (config.K_X_SPLIT_AMOUNT_BALANCE + config.K_X_TOLERANCE):
                                # Left of amount/balance split
                                if numeric_amount < 0:
                                    debit_value = -numeric_amount
                                else:
                                    credit_value = numeric_amount
                            elif x0 <= (config.K_X_SPLIT_BALANCE_CHANNEL + config.K_X_TOLERANCE):
                                balance_value = numeric_amount

                        # d) TRANSACTION_TYPE_TOKEN
                        elif (config.K_DATE_X1 + config.K_X_TOLERANCE) < x0 <= config.K_X_SPLIT_DESC_AMOUNT:
                            transaction_type_tokens.append(text)

                        # e) CHANNEL_TOKEN
                        elif (config.K_X_SPLIT_DESC_AMOUNT + config.K_X_TOLERANCE) < x0 <= config.K_X_SPLIT_CHANNEL_DETAILS:
                            channel_tokens.append(text)

                        # f) DESCRIPTION_TOKEN
                        else:
                            detail_tokens.append(text)

                    transaction_records.append({
                        "page_id":         page_id_value,
                        "date":            date_value,
                        "time":            time_value,
                        "description":     " ".join(detail_tokens).strip(),
                        "withdrawal":      debit_value,
                        "deposit":         credit_value,
                        "balance":         balance_value,
                        "channel":         " ".join(channel_tokens).strip(),
                        "transaction_type": " ".join(transaction_type_tokens).strip(),
                    })

            except Exception as e:
                print(f"[!] Skipped transactions on page {page_number} due to: {e}")
                continue

        if transaction_records:
            raw_transaction_df = pd.DataFrame(transaction_records).copy()
        else:
            cols = [
                "page_id", "date", "time", "description",
                "withdrawal", "deposit", "balance",
                "channel", "transaction_type"
            ]
            raw_transaction_df = pd.DataFrame(columns=cols).copy()

        return raw_transaction_df

    # -------------------------------------------------------------------------
    # 3) CLEAN & FINALIZE
    # -------------------------------------------------------------------------
    @staticmethod
    def clean_dataframes(
        raw_headers_df: pd.DataFrame,
        raw_transactions_df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Take raw_headers_df and raw_transactions_df (both un‐cleaned and raw),
        then:
          • Rename header columns to canonical names,
          • Add placeholder “address” column,
          • Re‐order header columns,
          • In transactions: split “description” into (“date”, “rest_of_desc”), normalize dates,
            add “code” placeholder, rename ("withdrawal","deposit") → ("debit","credit"),
          • Clean all float columns with clean_float_column(...)
        Returns (clean_header_df, clean_transaction_df).
        """
        # --- HANDLE HEADERS ---
        header_df = raw_headers_df.copy()

        # 3a) Rename header columns to canonical form
        header_df = header_df.rename(columns={
            "total_withdrawal":             "total_debit",
            "total_deposit":                "total_credit",
            "total_withdrawal_transaction": "total_debit_transaction",
            "total_deposit_transaction":    "total_credit_transaction"
        })

        # 3b) Add a placeholder “address” column
        header_df["address"] = ""

        # 3c) Re‐order to keep only the desired columns in final order
        final_header_columns = [
            "page_id",
            "account_name",
            "account_number",
            "period",
            "total_debit",
            "total_credit",
            "total_debit_transaction",
            "total_credit_transaction",
            "address"
        ]
        header_df = header_df[final_header_columns].copy()

        # 3d) Clean numeric header columns
        for col in ["total_debit", "total_credit", "total_debit_transaction", "total_credit_transaction"]:
            if col in header_df:
                header_df[col] = TTBdetailExtractor.clean_float_column(header_df[col])

        # --- HANDLE TRANSACTIONS ---
        transaction_df = raw_transactions_df.copy()

        if not transaction_df.empty:
            # 3e) Split “description” into two parts: (“date”, “rest_of_description”)
            split_series = transaction_df["description"].apply(
                lambda full_text: pd.Series(TTBdetailExtractor.split_details_date(full_text))
            )
            split_series.columns = ["date_part", "description_rest"]
            transaction_df[["date", "description"]] = split_series[["date_part", "description_rest"]].copy()

            # 3f) Normalize the newly created “date” column
            transaction_df["date"] = transaction_df["date"].apply(TTBdetailExtractor.normalize_thai_eng_date)

            # 3g) Add a placeholder “code” column
            transaction_df["code"] = ""

            # 3h) Rename withdrawal/deposit → debit/credit, and re‐order columns
            transaction_df = transaction_df.rename(columns={
                "withdrawal": "debit",
                "deposit":    "credit"
            })
            final_transaction_columns = [
                "page_id",
                "date",
                "time",
                "code",
                "channel",
                "debit",
                "credit",
                "balance",
                "description",
                "transaction_type"
            ]
            transaction_df = transaction_df[final_transaction_columns].copy()

            # 3i) Clean numeric columns in transactions
            for col in ["debit", "credit", "balance"]:
                if col in transaction_df:
                    transaction_df[col] = TTBdetailExtractor.clean_float_column(transaction_df[col])

        return header_df, transaction_df

    # -------------------------------------------------------------------------
    # INTERNAL HELPERS (all static)
    # -------------------------------------------------------------------------
    @staticmethod
    def clean_float_column(series: pd.Series) -> pd.Series:
        """
        Given a pandas Series of (string or numeric) values, strip away any
        non‐numeric characters (commas, currency symbols), handle multiple “.” or “-”,
        and return a float Series (NaN if parsing fails).
        """
        def clean_value(raw_val: str) -> str:
            txt = str(raw_val)
            txt = re.sub(r"[^\d\.-]", "", txt)  # remove anything except digits, “.”, “-”
            if "-" in txt:
                txt = "-" + txt.replace("-", "")
            if "." in txt:
                parts = txt.split(".")
                txt = parts[0] + "." + "".join(parts[1:])
            return txt

        return pd.to_numeric(series.astype(str).apply(clean_value), errors="coerce")

    @staticmethod
    def compute_date_tops(words: List[Dict]) -> List[float]:
        """
        Identify each vertical “top” at which a valid date trio (day, month, year) appears.
        Returns a list of “top” values (floats).
        """
        top_positions: List[float] = []
        for i in range(len(words) - 2):
            d, m, y = words[i], words[i + 1], words[i + 2]
            if (
                re.match(r"^\d{1,2}$", d["text"]) and
                re.match(r"^[^\s]+$", m["text"]) and
                re.match(r"^\d{2}$", y["text"]) and
                (d["x0"] < 100.0)
            ):
                top_positions.append(d["top"])
        return top_positions

    @staticmethod
    def compute_intervals(date_tops: List[float]) -> List[Tuple[float, float]]:
        """
        Given a sorted list of date‐top positions, create vertical intervals:
        [(start1, end1), (start2, end2), …] so that each row’s words can be bucketed.
        """
        intervals: List[Tuple[float, float]] = []
        for idx, top_val in enumerate(sorted(date_tops)):
            start_y = top_val - config.K_Y_MARGIN
            if idx + 1 < len(date_tops):
                end_y = date_tops[idx + 1] - config.K_Y_MARGIN
            else:
                # Estimate a final “end” if this is the last top
                delta = (top_val - date_tops[idx - 1]) if idx > 0 else (config.K_Y_MARGIN * 2)
                end_y = top_val + delta - config.K_Y_MARGIN

            intervals.append((start_y, end_y))

        return intervals

    @staticmethod
    def assign_to_rows(
        words: List[Dict],
        intervals: List[Tuple[float, float]]
    ) -> List[List[Dict]]:
        """
        Distribute each word into exactly one of the vertical “interval” buckets,
        based on its “top” value. Returns a list of lists of word‐dicts.
        """
        row_buckets: List[List[Dict]] = [[] for _ in intervals]
        for w in words:
            for idx, (start_y, end_y) in enumerate(intervals):
                if start_y <= w["top"] < end_y:
                    row_buckets[idx].append(w)
                    break
        return row_buckets

    @staticmethod
    def split_details_date(details: str) -> Tuple[str, str]:
        """
        Given a single string “details” (e.g. "1 ม.ค. 68 Some details here…"),
        split into (“1 ม.ค. 68”, “Some details here…”). If fewer than 3 tokens,
        the entire string is returned as the date‐part.
        """
        tokens = (details or "").split()
        if len(tokens) >= 3:
            date_part = " ".join(tokens[:3])
            rest_part = " ".join(tokens[3:]) if len(tokens) > 3 else ""
        else:
            date_part = details
            rest_part = ""
        return date_part, rest_part

    @staticmethod
    def normalize_thai_eng_date(date_str: str) -> str:
        """
        Convert a date string in Thai‐month or Eng‐month short format (e.g. "1 ม.ค. 68" or "1 Jan 23")
        to "DD-MM-YYYY". If parsing fails, returns the original string.
        """
        if not isinstance(date_str, str) or not date_str.strip():
            return date_str

        tokens = date_str.strip().replace("  ", " ").split()
        if len(tokens) == 3:
            day_token, month_token, year_token = tokens
            is_thai = month_token in config.THAI_MONTHS
            if is_thai:
                month_number = config.THAI_MONTHS[month_token]
            else:
                month_number = config.ENG_MONTHS.get(month_token[:3].capitalize(), "01")

            try:
                year_int = int(year_token)
                if is_thai:
                    # Thai two‐digit year (e.g. "68") → add 2500–543 to convert BE→CE
                    if year_int < 100:
                        year_int = year_int + 2500 - 543
                else:
                    # English two‐digit year (e.g. "23") → add 2000 → 2023
                    if year_int < 100:
                        year_int = year_int + 2000

                return f"{int(day_token):02d}-{month_number}-{year_int}"
            except Exception:
                pass

        return date_str
