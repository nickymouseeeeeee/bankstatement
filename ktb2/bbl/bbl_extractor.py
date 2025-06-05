# extractor.py

import re
import pdfplumber
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
#from tabulate import tabulate

# Import everything from config at once—our static methods will refer to config.<CONSTANT>
import config


class BBLStatementExtractor:
    """
    Extract headers and transactions from a BBL PDF statement.

    All helper methods are @staticmethod.  Only run() accepts
    a pdf_path and password as arguments.
    """

    # Compile regexes once at the class level so static methods can use them:
    _date_regex = re.compile(config.DATE_REGEX_PATTERN)
    _time_regex = re.compile(config.TIME_REGEX_PATTERN)
    _money_regex = re.compile(config.MONEY_REGEX_PATTERN)
    _page_regex = re.compile(config.PAGE_REGEX_PATTERN)

    @staticmethod
    def extract_page_id(page: pdfplumber.page.Page) -> str:
        """
        Crop and parse the page ID for pagination like '1/5'.
        Relies on config.PAGE_REGEX_PATTERN to find something like '1/5' at the bottom.
        """
        try:
            width, height = page.width, page.height
            # coordinates: (x0, y0, x1, y1)
            crop_box = (width - 180, height - 25, width, height)
            raw_text = page.crop(crop_box).extract_text() or ""
            single_line = raw_text.replace("\n", " ")
            match = BBLStatementExtractor._page_regex.search(single_line)
            return f"{match.group(1)}/{match.group(2)}" if match else ""
        except Exception:
            return ""

    @staticmethod
    def extract_headers(pages: List[pdfplumber.page.Page]) -> List[Dict[str, Any]]:
        """
        Extract header information from each page.
        Each page is cropped at various boxes (config.CROPS) to pull out fields
        like account number, statement date, etc.  Then look for FOOTER_KEYS
        (e.g. "จํานวนรายการถอน" / "จํานวนรายการฝาก") to grab totals.
        """
        header_records: List[Dict[str, Any]] = []

        for page in pages:
            page_id = BBLStatementExtractor.extract_page_id(page)
            try:
                full_text = page.extract_text() or ""
                record: Dict[str, Any] = {"page_id": page_id}

                # For each field defined in config.CROPS, crop and strip text.
                for field_name, box in config.CROPS.items():
                    text = page.crop(box).extract_text() or ""
                    record[field_name] = text.strip().replace("\n", " ")

                # If any of the FOOTER_KEYS appear in the page text, parse totals.
                if any(key in full_text for key in config.FOOTER_KEYS):
                    for line in full_text.splitlines():
                        if line.startswith("จํานวนรายการถอน"):
                            numbers = re.findall(r"[\d,]+(?:\.\d{2})?", line)
                            record["total_withdrawal_transaction"] = (
                                float(numbers[0].replace(",", "")) if len(numbers) > 0 else None
                            )
                            record["total_withdrawal"] = (
                                float(numbers[1].replace(",", "")) if len(numbers) > 1 else None
                            )
                        elif line.startswith("จํานวนรายการฝาก"):
                            numbers = re.findall(r"[\d,]+(?:\.\d{2})?", line)
                            record["total_deposit_transaction"] = (
                                float(numbers[0].replace(",", "")) if len(numbers) > 0 else None
                            )
                            record["total_deposit"] = (
                                float(numbers[1].replace(",", "")) if len(numbers) > 1 else None
                            )

                header_records.append(record)

            except Exception as error:
                print(f"⚠️  Skipping page {page_id} due to error: {error}")
                continue

        return header_records

    @staticmethod
    def extract_transactions(pages: List[pdfplumber.page.Page]) -> List[Dict[str, Any]]:
        """
        Extract transaction rows from the table region on each page. 
        We first detect all words in the table crop (config.TABLE_CROP_BOX),
        group them into rows whenever we see a DATE_REGEX match, then parse out
        date, time, description, channel, debit/credit, and balance.
        """
        transaction_records: List[Dict[str, Any]] = []

        for page in pages:
            try:
                page_id = BBLStatementExtractor.extract_page_id(page)

                # Take the crop box defined in config.TABLE_CROP_BOX,
                # but make sure it fits within the page dimensions:
                crop_x0, crop_y0, crop_x1, crop_y1 = config.TABLE_CROP_BOX
                page_width, page_height = page.width, page.height
                safe_crop_box: Tuple[float, float, float, float] = (
                    crop_x0,
                    crop_y0,
                    min(crop_x1, page_width),
                    min(crop_y1, page_height),
                )
                region = page.crop(safe_crop_box)

                # Extract words with a small tolerance so we can cluster them into rows:
                words = region.extract_words(
                    x_tolerance=3, 
                    y_tolerance=3, 
                    use_text_flow=True
                )

                # Find every 'top' coordinate where the text matches a date pattern:
                row_tops = sorted(
                    w["top"] 
                    for w in words 
                    if BBLStatementExtractor._date_regex.match(w["text"])
                )
                print(f"Page {page_id}: found {len(row_tops)} date entries")

                if not row_tops:
                    # No date => no transactions on this page
                    continue

                # Build intervals for each row: each interval spans from row_tops[i] - Y_MARGIN
                # up to row_tops[i+1] - Y_MARGIN (or +15px at the end).
                intervals = [
                    (
                        row_tops[i] - config.Y_MARGIN,
                        row_tops[i + 1] - config.Y_MARGIN 
                        if i + 1 < len(row_tops) 
                        else row_tops[i] + 15,
                    )
                    for i in range(len(row_tops))
                ]

                # Now allocate each word into the correct row index:
                rows: List[List[Dict[str, Any]]] = [[] for _ in intervals]
                for w in words:
                    for idx, (top_min, top_max) in enumerate(intervals):
                        if top_min <= w["top"] < top_max:
                            rows[idx].append(w)
                            break

                # For each row group, parse date, time, description, channel, amounts, etc.
                for idx, row in enumerate(rows):
                    if not row:
                        continue

                    row_text = " ".join(w["text"] for w in row)
                    # If this row matches any FOOTER_KEYS, skip it:
                    if any(key in row_text for key in config.FOOTER_KEYS):
                        continue

                    # Sort words by their vertical (then horizontal) positions:
                    sorted_row = sorted(row, key=lambda w: (w["top"], w["x0"]))

                    # Find the date word in this row
                    date_word = next(
                        (w for w in sorted_row if BBLStatementExtractor._date_regex.match(w["text"])),
                        None,
                    )
                    date_value = (
                        pd.to_datetime(
                            date_word["text"], 
                            format="%d/%m/%y", 
                            dayfirst=True, 
                            errors="coerce"
                        ) 
                        if date_word 
                        else None
                    )

                    # Find a time word on nearly the same 'top' line:
                    time_word = next(
                        (
                            w 
                            for w in sorted_row 
                            if BBLStatementExtractor._time_regex.match(w["text"]) 
                            and abs(w["top"] - (date_word["top"] if date_word else 0)) < 20
                        ),
                        None,
                    )
                    time_value = time_word["text"] if time_word else ""

                    # We will bucket tokens into: description_tokens, channel_via_tokens, withdrawal_tokens, balance_tokens
                    description_tokens: List[str] = []
                    channel_via_tokens: List[str] = []
                    withdrawal_tokens: List[Tuple[str, float]] = []
                    balance_tokens: List[str] = []

                    for w in sorted_row:
                        text, x0, x1 = w["text"], w["x0"], w["x1"]

                        # Skip over actual date/time tokens
                        if BBLStatementExtractor._date_regex.match(text) or BBLStatementExtractor._time_regex.match(text):
                            continue

                        # If it looks like money (e.g. "1,234.56"):
                        if BBLStatementExtractor._money_regex.match(text):
                            # If it falls to the left of the CHANNEL/DC split:
                            if x1 <= config.X_SPLIT_CHANNEL_DC + config.X_TOLERANCE:
                                withdrawal_tokens.append((text, x1))
                            else:
                                balance_tokens.append(text)
                            continue

                        # If x0 is very far left, treat it as description
                        if x0 <= config.X_SPLIT_CODE_CHANNEL + config.X_TOLERANCE:
                            description_tokens.append(text)
                        # If x0 is far right, treat it as channel/via
                        elif x0 >= config.X_SPLIT_CHANNEL_VIA - config.X_TOLERANCE:
                            channel_via_tokens.append(text)
                        else:
                            description_tokens.append(text)

                    channel_value = " ".join(channel_via_tokens).strip()

                    withdrawal_amount = None
                    deposit_amount = None
                    # Among all withdrawal_tokens, decide which one is 'withdrawal' vs 'deposit'
                    for value_str, x1 in withdrawal_tokens:
                        value = float(value_str.replace(",", ""))
                        if x1 <= config.X_SPLIT_WITHDRAW_DEP + config.X_TOLERANCE:
                            withdrawal_amount = value
                        else:
                            deposit_amount = value

                    balance_amount = (
                        float(balance_tokens[-1].replace(",", "")) 
                        if balance_tokens 
                        else None
                    )

                    transaction_records.append({
                        "page_id": page_id,
                        "date": date_value,
                        "time": time_value,
                        "description": "",
                        "channel": channel_value,
                        "withdrawal": withdrawal_amount,
                        "deposit": deposit_amount,
                        "balance": balance_amount,
                        "transaction_type": " ".join(description_tokens).strip(),
                    })

                print(f"Page {page_id}: extracted {len(transaction_records)} transactions so far")

            except Exception as error:
                print(f"⚠️ Skipping transactions on page {getattr(page, 'page_number', '?')}: {error}")
                continue

        return transaction_records

    @staticmethod
    def clean_float_column(
        header_dataframe: pd.DataFrame,
        transaction_dataframe: pd.DataFrame,
    ) -> None:
        """
        Clean and convert all numeric text columns to floats in both DataFrames.
        This mutates header_dataframe and transaction_dataframe in-place.
        """
        float_columns_transactions = ["withdrawal", "deposit", "balance"]
        for column in float_columns_transactions:
            if column in transaction_dataframe.columns:
                transaction_dataframe[column] = (
                    transaction_dataframe[column]
                    .astype(str)
                    .str.replace(r"[^\d.]", "", regex=True)
                    .replace({"": np.nan, "NA": np.nan})
                    .astype(float)
                )

        float_columns_headers = [
            "total_withdrawal_transaction",
            "total_withdrawal",
            "total_deposit_transaction",
            "total_deposit",
        ]
        for column in float_columns_headers:
            if column in header_dataframe.columns:
                header_dataframe[column] = (
                    header_dataframe[column]
                    .astype(str)
                    .str.replace(r"[^\d.]", "", regex=True)
                    .replace({"": np.nan, "NA": np.nan})
                    .astype(float)
                )

    @staticmethod
    def clean_extracted_data(
        header_records: List[Dict[str, Any]],
        transaction_records: List[Dict[str, Any]],
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        1. Build DataFrames from raw header_records and transaction_records.
        2. Drop blank or malformed page_ids.
        3. Rename columns so 'withdrawal'→'debit', 'deposit'→'credit', etc.
        4. Reorder columns, then clean numeric fields via clean_float_column().
        """
        header_dataframe = pd.DataFrame(header_records)
        transaction_dataframe = pd.DataFrame(transaction_records)

        # Drop any header rows where page_id is blank or whitespace
        header_dataframe = header_dataframe[
            header_dataframe["page_id"].str.strip() != ""
        ].copy()

        # Ensure an 'address' column always exists (some statements may or may not have it)
        header_dataframe["address"] = ""

        # Rename total fields
        header_dataframe = header_dataframe.rename(
            columns={
                "total_withdrawal": "total_debit",
                "total_deposit": "total_credit",
                "total_withdrawal_transaction": "total_debit_transaction",
                "total_deposit_transaction": "total_credit_transaction",
            }
        ).reset_index(drop=True)

        # Filter out any page_ids that don't start with a digit (malformed)
        header_dataframe = header_dataframe[
            header_dataframe["page_id"].str.match(r"^\d", na=False)
        ].copy()

        # Keep only rows in transaction_dataframe that have either a withdrawal or deposit
        transaction_dataframe = transaction_dataframe[
            (~transaction_dataframe["withdrawal"].isnull())
            | (~transaction_dataframe["deposit"].isnull())
        ].copy()

        # Rename columns to standard names:
        transaction_dataframe = transaction_dataframe.rename(
            columns={"withdrawal": "debit", "deposit": "credit"}
        )

        # Reorder columns into a fixed schema
        transaction_dataframe = transaction_dataframe[
            [
                "page_id",
                "date",
                "time",
                "description",
                "channel",
                "debit",
                "credit",
                "balance",
                "transaction_type",
            ]
        ].copy()

        # Finally, clean all the numeric columns in both DataFrames
        BBLStatementExtractor.clean_float_column(header_dataframe, transaction_dataframe)

        return header_dataframe, transaction_dataframe

    @staticmethod
    def run(
        pdf_path: str,
        password: Optional[str] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Execute extraction and return cleaned DataFrames.
        This is the only method that requires you to pass in the PDF path and password.
        """
        with pdfplumber.open(pdf_path, password=password) as pdf:
            pages = pdf.pages
            header_list = BBLStatementExtractor.extract_headers(pages)
            transaction_list = BBLStatementExtractor.extract_transactions(pages)

        return BBLStatementExtractor.clean_extracted_data(
            header_list, transaction_list
        )


# Example usage:
# ---------------------------------------------
# from extractor import BBLStatementExtractor
#
# pdf_file = "/path/to/your/BBL_statement.pdf"
# pwd = "your_optional_password"
# headers_df, transactions_df = BBLStatementExtractor.run(pdf_file, pwd)
#
# print(f"Total headers extracted: {len(headers_df)}")
# print(tabulate(headers_df, headers='keys', tablefmt='psql', showindex=False))
# print(f"Total transactions extracted: {len(transactions_df)}")
# print(tabulate(transactions_df, headers='keys', tablefmt='psql', showindex=False))
