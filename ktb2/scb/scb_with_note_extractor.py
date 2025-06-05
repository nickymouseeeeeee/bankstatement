import re
from typing import Optional, List, Dict, Any, Tuple

import pandas as pd
import pdfplumber

import config  # Imports all constants and regex patterns


class SCBwithnoteStatementExtractor:
    """
    SCBwithnoteStatementExtractor encapsulates:
      1. Header extraction (account name, account number, period, totals)
      2. Transaction extraction (date, time, code, channel, debit, credit, balance, description)
      3. Cleanup to coerce numeric strings into floats
    """

    @staticmethod
    def clean_page_id(raw_text: str) -> str:
        """
        Extracts and normalizes page ID from a string of text.
        Returns "n/m" if found, else "".
        """
        found_numbers = re.findall(r"\d+", raw_text)
        if len(found_numbers) >= 2:
            candidate = f"{found_numbers[0]}/{found_numbers[1]}"
            if re.fullmatch(r"\d+/\d+", candidate):
                return candidate
        return ""

    @staticmethod
    def extract_page_id_from_page(page: pdfplumber.page.Page) -> str:
        """
        Crops a small region where the page ID is printed (e.g. "1/7"), then cleans it.
        """
        cropped = page.crop(config.PAGE_ID_CROP_BOX)
        raw_text = cropped.extract_text() or ""
        return SCBwithnoteStatementExtractor.clean_page_id(raw_text.strip())

    @staticmethod
    def find_time_for_row(
        date_word: Dict[str, Any],
        all_words: List[Dict[str, Any]]
    ) -> str:
        """
        Given a dict for a date word, search below it (within 20 pixels)
        for a TIME_REGEX match and return that time string.
        """
        if date_word is None:
            return ""
        date_top = date_word.get("top", 0)
        for word in all_words:
            text = word.get("text", "")
            if config.TIME_REGEX.match(text):
                vertical_offset = word["top"] - date_top
                if 0 < vertical_offset <= 20:
                    return text
        return ""

    @staticmethod
    def extract_transactions(
        pages: List[pdfplumber.page.Page]
    ) -> pd.DataFrame:
        """
        Goes through all pages, finds transaction rows, and builds a DataFrame of:
          [page_id, date, time, code, channel, debit, credit, balance, description]
        Any errors on a given page are caught and printed; extraction then continues.
        """
        transaction_records: List[Dict[str, Any]] = []

        for page_index, page in enumerate(pages, start=1):
            try:
                page_id = SCBwithnoteStatementExtractor.extract_page_id_from_page(page)
                table_region = page.crop(config.TABLE_CROP_BOX)
                all_words = table_region.extract_words(use_text_flow=False)

                # Identify Y‐positions of every word whose text matches a date and is within the date column x‐range
                date_tops = sorted(
                    word["top"]
                    for word in all_words
                    if config.DATE_REGEX.match(word["text"])
                    and config.DATE_COLUMN_X0 <= word["x0"] <= config.DATE_COLUMN_X1
                )
                if not date_tops:
                    # No transactions on this page
                    continue

                # Build vertical intervals for each row
                row_intervals: List[Tuple[float, float]] = []
                for idx, y in enumerate(date_tops):
                    top_bound = y - config.Y_MARGIN
                    if idx + 1 < len(date_tops):
                        bottom_bound = date_tops[idx + 1] - config.Y_MARGIN
                    else:
                        bottom_bound = y + 15  # Enough to catch associated words
                    row_intervals.append((top_bound, bottom_bound))

                # Group words into their respective rows
                rows: List[List[Dict[str, Any]]] = [[] for _ in row_intervals]
                for word in all_words:
                    word_top = word["top"]
                    for row_id, (row_top, row_bottom) in enumerate(row_intervals):
                        if row_top <= word_top < row_bottom:
                            rows[row_id].append(word)
                            break

                # For each row, parse columns
                for row_words in rows:
                    if not row_words:
                        continue

                    # Merge all text in the row to skip footers if found
                    row_text_combined = " ".join(w["text"] for w in row_words)
                    if any(keyword in row_text_combined for keyword in config.FOOTER_KEYWORDS):
                        continue  # skip summary/footer row

                    # Sort by vertical then horizontal so left‐to‐right reading order is preserved
                    row_sorted = sorted(row_words, key=lambda w: (w["top"], w["x0"]))

                    # Find the "date" word and then any time immediately below
                    date_word = next(
                        (
                            w for w in row_sorted
                            if config.DATE_REGEX.match(w["text"])
                            and config.DATE_COLUMN_X0 <= w["x0"] <= config.DATE_COLUMN_X1
                        ),
                        None
                    )
                    date_string = date_word["text"] if date_word else ""
                    time_string = (
                        SCBwithnoteStatementExtractor.find_time_for_row(date_word, all_words) if date_word else ""
                    )

                    # Prepare containers for each column
                    code_tokens: List[str] = []
                    channel_tokens: List[str] = []
                    description_tokens: List[str] = []
                    debit_credit_words: List[Dict[str, Any]] = []
                    balance_words: List[Dict[str, Any]] = []

                    # Classify each word by its x‐position
                    for word in row_sorted:
                        text = word["text"]
                        x0 = word["x0"]

                        # Skip date/time itself
                        if config.DATE_REGEX.match(text) or config.TIME_REGEX.match(text):
                            continue

                        # Monetary values: decide if it's debit/credit vs balance
                        if config.MONEY_REGEX.match(text):
                            if x0 <= config.X_SPLIT_CHANNEL_DEBIT_CREDIT + config.X_TOLERANCE:
                                debit_credit_words.append(word)
                            else:
                                balance_words.append(word)
                            continue

                        # Otherwise, decide if this is code, channel, or description
                        if x0 <= config.X_SPLIT_CODE_CHANNEL + config.X_TOLERANCE:
                            code_tokens.append(text)
                        elif x0 <= config.X_SPLIT_CHANNEL_DEBIT_CREDIT + config.X_TOLERANCE:
                            channel_tokens.append(text)
                        else:
                            description_tokens.append(text)

                    # Combine code + channel fields
                    combined_code_channel = "/".join(code_tokens + channel_tokens)
                    code_value, channel_value = (
                        (combined_code_channel.split("/", 1) + [""])[:2]
                    )

                    # Determine withdrawal and deposit amounts
                    withdrawal_amount: Optional[float] = None
                    deposit_amount: Optional[float] = None
                    for word in debit_credit_words:
                        numeric_value = float(word["text"].replace(",", ""))
                        # If the right edge (x1) is to the left of the withdrawal/deposit split, treat as withdrawal
                        if word["x1"] <= config.X_SPLIT_WITHDRAWAL_DEPOSIT + config.X_TOLERANCE:
                            withdrawal_amount = numeric_value
                        else:
                            deposit_amount = numeric_value

                    # Pick the rightmost monetary word for balance (if present)
                    balance_amount: Optional[float] = None
                    if balance_words:
                        rightmost = max(balance_words, key=lambda w: w["x0"])
                        balance_amount = float(rightmost["text"].replace(",", ""))

                    # Assemble the record dictionary
                    record: Dict[str, Any] = {
                        "page_id": page_id,
                        "date": pd.to_datetime(date_string, format="%d/%m/%y", dayfirst=True, errors="coerce"),
                        "time": time_string,
                        "code": code_value,
                        "channel": channel_value,
                        "debit": withdrawal_amount,
                        "credit": deposit_amount,
                        "balance": balance_amount,
                        "description": " ".join(description_tokens),
                    }

                    # If page_id is blank, make every field blank
                    if page_id == "":
                        record = {key: "" for key in record}

                    transaction_records.append(record)

            except Exception as error:
                print(f"⚠️  Skipping page {page_index} in transaction extraction due to error: {error}")
                continue

        transaction_dataframe = pd.DataFrame(transaction_records)
        return transaction_dataframe

    @staticmethod
    def extract_headers(
        pages: List[pdfplumber.page.Page]
    ) -> pd.DataFrame:
        """
        Extracts header information (account_name, account_number, period),
        plus any "Total amount" or "Total items" if a footer exists.
        """
        header_rows_list: List[Dict[str, Any]] = []

        for page_index, page in enumerate(pages, start=1):
            try:
                page_id = SCBwithnoteStatementExtractor.extract_page_id_from_page(page)
                full_page_text = page.extract_text() or ""
                footer_present = any(
                    kw in full_page_text.lower() for kw in config.FOOTER_KEYWORDS_LOWER
                )

                header_data: Dict[str, Any] = {"page_id": page_id}

                # Extract each header field from its crop box
                for field_name, bbox in config.CROP_BOXES.items():
                    cropped_field = page.crop(bbox)
                    raw_field_text = cropped_field.extract_text() or ""
                    header_data[field_name] = raw_field_text.strip().replace("\n", " ")

                # If a footer exists, parse totals from any line starting with "Total amount" or "Total items"
                if footer_present:
                    for line in full_page_text.splitlines():
                        if line.startswith("Total amount"):
                            found_numbers = re.findall(r"[\d,]+(?:\.\d{2})?", line)
                            header_data.update(
                                {
                                    "total_amount_debit": found_numbers[0].replace(",", "") if len(found_numbers) > 0 else None,
                                    "total_amount_credit": found_numbers[1].replace(",", "") if len(found_numbers) > 1 else None,
                                }
                            )
                        elif line.startswith("Total items"):
                            found_integers = re.findall(r"\d+", line)
                            header_data.update(
                                {
                                    "total_items_debit": found_integers[0] if len(found_integers) > 0 else None,
                                    "total_items_credit": found_integers[1] if len(found_integers) > 1 else None,
                                }
                            )

                # If page_id is blank, zero‐out every header field
                if page_id == "":
                    header_data = {key: "" for key in header_data}

                header_rows_list.append(header_data)

            except Exception as error:
                print(f"⚠️  Skipping page {page_index} in header extraction due to error: {error}")
                continue

        headers_dataframe = pd.DataFrame(header_rows_list)
        # Add an empty 'address' column (to match previous structure)
        headers_dataframe["address"] = ""
        return headers_dataframe

    @staticmethod
    def clean_float_column(series: pd.Series) -> pd.Series:
        """
        Strips all non-numeric characters (except the dot) from a string Series,
        then converts to float. Non-convertible strings become NaN.
        """
        stripped = series.astype(str).apply(lambda s: re.sub(r"[^0-9\.]", "", s))
        return pd.to_numeric(stripped, errors="coerce")
    

    @staticmethod
    def clean_dataframes(transaction_dataframe: pd.DataFrame, header_dataframe: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        1. Renames header columns and fills missing values
        2. Filters out invalid header rows (where page_id does not start with a digit)
        3. Renames transaction columns, fills missing, and ensures numeric columns are float dtype
        4. Always use .copy() when slicing
        """
        # ── CLEAN HEADERS ──────────────────────────────────────────────────────
        header_dataframe = header_dataframe.rename(
            columns={
                "total_amount_debit":  "total_debit",
                "total_amount_credit": "total_credit",
                "total_items_debit":   "total_debit_transaction",
                "total_items_credit":  "total_credit_transaction",
            }
        ).fillna("")

        # Keep only rows where page_id starts with a digit, then copy
        header_dataframe = (
            header_dataframe[
                header_dataframe["page_id"].str.match(r"^\d", na=False)
            ]
            .copy()
            .reset_index(drop=True)
        )

        # ── CLEAN TRANSACTIONS ─────────────────────────────────────────────────
        transaction_dataframe = (
            transaction_dataframe.rename(
                columns={"withdrawal": "debit", "deposit": "credit"}
            )
            .fillna("")  # Fill NaN with empty string before coercion
            .assign(transaction_type="")  # Add an extra column if needed later
        )

        # Keep only valid transactions (page_id not blank), then copy
        transaction_dataframe = (
            transaction_dataframe[transaction_dataframe["page_id"] != ""]
            .copy()
        )

        # Convert debit, credit, balance to floats
        columns_to_float_tx = ["debit", "credit", "balance"]
        for column_name in columns_to_float_tx:
            if column_name in transaction_dataframe:
                transaction_dataframe[column_name] = SCBwithnoteStatementExtractor.clean_float_column(
                    transaction_dataframe[column_name]
                )

        # Convert numeric header columns to floats
        header_columns_to_float = [
            "total_debit",
            "total_credit",
            "total_debit_transaction",
            "total_credit_transaction",
        ]
        for column_name in header_columns_to_float:
            if column_name in header_dataframe:
                header_dataframe[column_name] = SCBwithnoteStatementExtractor.clean_float_column(
                    header_dataframe[column_name]
                )

        return header_dataframe, transaction_dataframe
    
    @staticmethod
    def run(pdf_path: Optional[str] = None, password: Optional[str] = None
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Opens the PDF, runs transaction & header extraction, then cleans both DataFrames.
        Returns (cleaned_header_dataframe, cleaned_transaction_dataframe).
        """
        pdf_path = pdf_path
        password = password 

        with pdfplumber.open(pdf_path, password=password) as pdf:
            page_list = pdf.pages
            raw_transaction_dataframe = SCBwithnoteStatementExtractor.extract_transactions(page_list)
            raw_header_dataframe = SCBwithnoteStatementExtractor.extract_headers(page_list)

        cleaned_header_dataframe, cleaned_transaction_dataframe = SCBwithnoteStatementExtractor.clean_dataframes(
            raw_transaction_dataframe, raw_header_dataframe
        )
        return cleaned_header_dataframe, cleaned_transaction_dataframe