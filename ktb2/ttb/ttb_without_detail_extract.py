import re
import pdfplumber
import pandas as pd
from typing import Optional, List, Dict, Tuple

import config_without_detail as config  # import everything; constants are accessed as config.<CONSTANT>


class TTBStatementExtractor:
    """
    - extract_headers(pages) → DataFrame of raw header fields (one row per page)
    - extract_transactions_from_pages(pages) → DataFrame of raw transaction dicts
    - clean_dataframes(raw_headers_df, raw_transactions_df) → (df_header_clean, df_transactions_clean)
    """

    @staticmethod
    def clean_float_column(series: pd.Series) -> pd.Series:
        """
        Remove non-numeric characters, fix minus signs, coerce to float.
        """
        def _clean_value(val):
            if pd.isnull(val):
                return None
            text = re.sub(r"[^\d\.-]", "", str(val))
            if "-" in text:
                text = "-" + text.replace("-", "")
            if "." in text:
                main, *rest = text.split(".")
                text = main + "." + "".join(rest)
            return text

        try:
            return pd.to_numeric(series.astype(str).apply(_clean_value), errors="coerce")
        except Exception as e:
            print(f"⚠️ Float cleaning error: {e}")
            return series

    @staticmethod
    def extract_header_from_page(
        page,
        crop_bounds: Dict[str, Tuple[float, float, float, float]]
    ) -> Dict[str, Optional[str]]:
        """
        Crop out each field in crop_bounds; if numeric, apply NUMERIC_REGEX.
        Returns a dict mapping field_name → raw string (or numeric string if matched).
        """
        header_dict: Dict[str, Optional[str]] = {}
        for field_name, bbox in crop_bounds.items():
            try:
                raw_text = page.crop(bbox).extract_text() or ""
                stripped = raw_text.strip()
                if field_name in config.NUMERIC_FIELDS:
                    m = config.NUMERIC_REGEX.search(stripped)
                    header_dict[field_name] = m.group().replace(",", "") if m else None
                else:
                    header_dict[field_name] = stripped
            except Exception as e:
                print(f"⚠️ Error extracting header '{field_name}': {e}")
                header_dict[field_name] = None

        return header_dict

    @staticmethod
    def compute_date_tops(words: List[dict]) -> List[float]:
        """
        Find y-coordinates ("top") of triplets [day, month, year] in the word list.
        """
        tops: List[float] = []
        for i in range(len(words) - 2):
            try:
                d, m, y = words[i], words[i + 1], words[i + 2]
                if (
                    re.match(r"^\d{1,2}$", d["text"])
                    and re.match(r"^[^\s]+$", m["text"])
                    and re.match(r"^\d{2}$", y["text"])
                    and d["x0"] < 100.0
                ):
                    tops.append(d["top"])
            except Exception:
                continue
        return tops

    @staticmethod
    def compute_intervals(date_tops: List[float]) -> List[Tuple[float, float]]:
        """
        Build vertical intervals out of the date_tops list. Each interval covers one row.
        """
        intervals: List[Tuple[float, float]] = []
        for idx, current_top in enumerate(date_tops):
            start = current_top - config.K_Y_MARGIN
            if idx + 1 < len(date_tops):
                end = date_tops[idx + 1] - config.K_Y_MARGIN
            else:
                if idx > 0:
                    prev_gap = current_top - date_tops[idx - 1]
                else:
                    prev_gap = config.K_Y_MARGIN * 2
                end = current_top + prev_gap - config.K_Y_MARGIN
            intervals.append((start, end))
        return intervals

    @staticmethod
    def assign_words_to_rows(
        words: List[dict],
        intervals: List[Tuple[float, float]]
    ) -> List[List[dict]]:
        """
        Given a list of word dictionaries and row‐intervals, place each word into its row.
        Returns a list of rows, where each row is a list of word dicts.
        """
        rows: List[List[dict]] = [[] for _ in intervals]
        for w in words:
            top_val = w["top"]
            for row_idx, (start, end) in enumerate(intervals):
                if start <= top_val < end:
                    rows[row_idx].append(w)
                    break
        return rows

    @staticmethod
    def split_details_into_date_and_details(details_text: str) -> Tuple[str, str]:
        """
        For a string like "10 ม.ค. 65 Purchase ABC", split off first three tokens as date.
        Returns (date_part, rest_of_details).
        """
        tokens = (details_text or "").split()
        if len(tokens) >= 3:
            date_part = " ".join(tokens[:3])
            rest_part = " ".join(tokens[3:])
        else:
            date_part = details_text
            rest_part = ""
        return date_part, rest_part

    @staticmethod
    def normalize_thai_or_eng_date(date_string: str) -> str:
        """
        Convert "D M Y" (Thai or English) into "YYYY-MM-DD". If parsing fails, return original.
        """
        if not isinstance(date_string, str) or not date_string.strip():
            return date_string

        tokens = date_string.strip().replace("  ", " ").split()
        if len(tokens) == 3:
            day_txt, mon_txt, year_txt = tokens
            is_thai = mon_txt in config.THAI_MONTHS

            if is_thai:
                mon_num = config.THAI_MONTHS[mon_txt]
            else:
                mon_num = config.ENG_MONTHS.get(mon_txt[:3].capitalize(), "01")

            try:
                year_int = int(year_txt)
                if is_thai:
                    if year_int < 100:
                        # "65" → 2565 BE → subtract 543 → 2022 CE
                        year_int = year_int + 2500 - 543
                else:
                    if year_int < 100:
                        year_int = year_int + 2000
                return f"{year_int:04d}-{mon_num}-{int(day_txt):02d}"
            except Exception:
                pass

        return date_string

    @staticmethod
    def extract_headers(pages) -> pd.DataFrame:
        """
        Loop over all pages, crop out each header field, build a list of dicts,
        then return pd.DataFrame(raw_header_list).
        """
        header_list: List[Dict[str, Optional[str]]] = []

        for page_index, page in enumerate(pages, start=1):
            try:
                # Extract raw header fields via cropping
                header_dict = TTBStatementExtractor.extract_header_from_page(page, config.CROP_BOUNDS)

                # Derive page_id (e.g. "1/10") from header_dict["page"]
                raw_page_field = header_dict.get("page", "")
                m = config.PAGE_ID_REGEX.search(raw_page_field)
                page_id = m.group(1) if m else ""
                header_dict["page_id"] = page_id

                # If not page "1/…", blank out all other fields (keep only page_id)
                if not page_id.startswith("1/"):
                    for key in list(header_dict.keys()):
                        if key != "page_id":
                            header_dict[key] = None

                header_list.append(header_dict)

            except Exception as e:
                print(f"⚠️ Skipping header on page {page_index} due to error: {e}")
                # Build an “empty” header dict with all crop keys set to None + page_id=None
                empty_header = {k: None for k in config.CROP_BOUNDS.keys()}
                empty_header["page_id"] = None
                header_list.append(empty_header)

        df_raw_headers = pd.DataFrame(header_list)
        return df_raw_headers

    @staticmethod
    def extract_transactions(pages) -> pd.DataFrame:
        """
        Loop over all pages, find transaction rows via word-level analysis,
        build a list of raw transaction dicts (un-cleaned), then return DataFrame.
        """
        transaction_records: List[Dict[str, Optional[str]]] = []

        for page_index, page in enumerate(pages, start=1):
            try:
                words = page.extract_words(use_text_flow=True)

                # Re-extract header to get page_id
                header_dict = TTBStatementExtractor.extract_header_from_page(page, config.CROP_BOUNDS)
                raw_page_field = header_dict.get("page", "")
                m = config.PAGE_ID_REGEX.search(raw_page_field)
                page_id = m.group(1) if m else ""

                # Compute row intervals
                date_tops = TTBStatementExtractor.compute_date_tops(words)
                if not date_tops:
                    continue  # no transactions on this page

                intervals = TTBStatementExtractor.compute_intervals(date_tops)
                rows_of_words = TTBStatementExtractor.assign_words_to_rows(words, intervals)

                # Parse each row into a raw dict
                for row_words in rows_of_words:
                    if not row_words:
                        continue

                    try:
                        sorted_row = sorted(row_words, key=lambda w: (w["top"], w["x0"]))

                        date_text = ""
                        time_text = ""
                        debit_amount = None
                        credit_amount = None
                        balance_amount = None
                        description_tokens: List[str] = []
                        channel_tokens: List[str] = []
                        details_tokens: List[str] = []

                        for w in sorted_row:
                            x0 = w["x0"]
                            txt = w["text"]

                            if config.DATE_REGEX.match(txt) and config.K_DATE_X0 <= x0 <= config.K_DATE_X1:
                                date_text = txt
                            elif config.TIME_REGEX.match(txt):
                                time_text = txt
                            elif config.MONEY_REGEX.match(txt):
                                val = float(txt.replace(",", ""))
                                if x0 <= config.K_X_SPLIT_AMOUNT_BALANCE + config.K_X_TOLERANCE:
                                    if val < 0:
                                        debit_amount = -val
                                    else:
                                        credit_amount = val
                                elif x0 <= config.K_X_SPLIT_BALANCE_CHANNEL + config.K_X_TOLERANCE:
                                    balance_amount = val
                            elif config.K_DATE_X1 + config.K_X_TOLERANCE < x0 <= config.K_X_SPLIT_DESC_AMOUNT:
                                description_tokens.append(txt)
                            elif config.K_X_SPLIT_DESC_AMOUNT + config.K_X_TOLERANCE < x0 <= config.K_X_SPLIT_CHANNEL_DETAILS:
                                channel_tokens.append(txt)
                            else:
                                details_tokens.append(txt)

                        transaction_records.append({
                            "page_id": page_id,
                            "date": date_text,
                            "time": time_text,
                            "description": "",  # to be filled in cleaning step
                            "withdrawal": debit_amount,
                            "deposit": credit_amount,
                            "balance": balance_amount,
                            "channel": " ".join(channel_tokens).strip(),
                            "details": " ".join(details_tokens).strip(),
                            "transaction_type": " ".join(description_tokens).strip()
                        })

                    except Exception as row_err:
                        print(f"⚠️ Skipping row on page {page_index} due to error: {row_err}")
                        continue

            except Exception as page_err:
                print(f"⚠️ Skipping page {page_index} due to error: {page_err}")
                continue

        df_raw_transactions = pd.DataFrame(transaction_records)
        return df_raw_transactions

    @staticmethod
    def clean_dataframes(
        raw_headers_df: pd.DataFrame,
        raw_transactions_df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        1) Rename header columns to canonical names
        2) Add placeholder 'address'
        3) Reorder header columns
        4) In transactions:
           - Split 'details' into [date, details]
           - Normalize date strings
           - Rename withdrawal→debit, deposit→credit
           - Add placeholder 'code'
           - Reorder transaction columns
           - Clean float columns
        Returns (df_header_clean, df_transactions_clean).
        """
        df_header = raw_headers_df.copy()
        df_transactions = raw_transactions_df.copy()

        # ── HEADER CLEANUP ────────────────────────────────────────────────────────
        df_header = df_header.rename(columns={
            "total_withdrawal": "total_debit",
            "total_deposit": "total_credit",
            "total_withdrawal_transaction": "total_debit_transaction",
            "total_deposit_transaction": "total_credit_transaction",
        })

        # Add placeholder 'address'
        df_header["address"] = ""

        desired_header_cols = [
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
        # Only keep columns that exist, in the specified order
        df_header = df_header.reindex(columns=desired_header_cols).copy()

        # Clean float columns in header
        float_cols_hdr = [
            "total_debit",
            "total_credit",
            "total_debit_transaction",
            "total_credit_transaction"
        ]
        for col in float_cols_hdr:
            if col in df_header:
                df_header[col] = TTBStatementExtractor.clean_float_column(df_header[col])

        # ── TRANSACTION CLEANUP ───────────────────────────────────────────────────
        if not df_transactions.empty:
            # 1) Split 'details' column into actual date + remainder
            split_series = df_transactions["details"].apply(
                lambda dt: pd.Series(TTBStatementExtractor.split_details_into_date_and_details(dt))
            )
            df_transactions = df_transactions.copy()
            df_transactions[["date", "details"]] = split_series

            # 2) Normalize the 'date' strings (Thai/ENG → YYYY-MM-DD)
            df_transactions["date"] = df_transactions["date"].apply(
                TTBStatementExtractor.normalize_thai_or_eng_date
            )

        # 3) Rename 'withdrawal'→'debit', 'deposit'→'credit'
        df_transactions = df_transactions.rename(columns={
            "withdrawal": "debit",
            "deposit":    "credit"
        })

        # 4) Add placeholder 'code'
        df_transactions["code"] = ""

        desired_txn_cols = [
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
        df_transactions = df_transactions.reindex(columns=desired_txn_cols).copy()

        # 5) Clean float columns in transactions
        float_cols_txn = ["debit", "credit", "balance"]
        for col in float_cols_txn:
            if col in df_transactions:
                df_transactions[col] = TTBStatementExtractor.clean_float_column(df_transactions[col])

        return df_header, df_transactions
    
    @staticmethod
    def run(pdf_path: str, password: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Opens the PDF at `pdf_path` (with optional `password`), extracts raw headers
        and transactions, cleans them, and returns (df_header_clean, df_transactions_clean).
        """
        with pdfplumber.open(pdf_path, password=password) as pdf:
            pages = pdf.pages

            raw_headers_df = TTBStatementExtractor.extract_headers(pages)
            raw_transactions_df = TTBStatementExtractor.extract_transactions(pages)
            df_header_clean, df_transactions_clean = TTBStatementExtractor.clean_dataframes(
                raw_headers_df,
                raw_transactions_df
            )

        return df_header_clean, df_transactions_clean