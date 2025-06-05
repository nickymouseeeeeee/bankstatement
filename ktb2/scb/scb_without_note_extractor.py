# File: extractor.py

import pdfplumber
import pandas as pd
from typing import List, Dict, Tuple, Optional

import config_without_note as config  # Contains all constants, regexes, and bounding boxes


class SCBStatementExtractor:

    # ─── HELPER METHODS ─────────────────────────────────────────────────

    @staticmethod
    def compute_date_top_coordinates(word_list: List[dict]) -> List[float]:
        """
        Finds all Y-coordinates of words matching the date pattern
        that fall within the configured X-range—used to detect row starts.
        """
        date_tops = sorted(
            word["top"]
            for word in word_list
            if config.DATE_PATTERN.match(word["text"])
            and config.DATE_X0 <= word["x0"] <= config.DATE_X1
        )
        return date_tops

    @staticmethod
    def compute_row_intervals(date_tops: List[float]) -> List[Tuple[float, float]]:
        """
        Converts each date Y-coordinate into a (start, end) interval on the Y-axis,
        with margins applied, so words can be grouped into logical rows.
        """
        intervals: List[Tuple[float, float]] = []
        for index, y_coord in enumerate(date_tops):
            start_y = y_coord - config.Y_MARGIN
            if index + 1 < len(date_tops):
                end_y = date_tops[index + 1] - config.Y_MARGIN
            else:
                previous_gap = (
                    (y_coord - date_tops[index - 1]) if index > 0 else config.Y_MARGIN * 2
                )
                end_y = y_coord + previous_gap - config.Y_MARGIN
            intervals.append((start_y, end_y))
        return intervals

    @staticmethod
    def assign_words_to_rows(
        word_list: List[dict], row_intervals: List[Tuple[float, float]]
    ) -> List[List[dict]]:
        """
        Given a list of words (each has 'top' and 'x0'), and a list of (start, end) Y-intervals,
        returns a list of lists, where each sublist contains all words whose 'top' falls in that interval.
        """
        grouped_rows: List[List[dict]] = [[] for _ in row_intervals]
        for word in word_list:
            top_y = word["top"]
            for idx, (start_y, end_y) in enumerate(row_intervals):
                if start_y <= top_y < end_y:
                    grouped_rows[idx].append(word)
                    break
        return grouped_rows

    @staticmethod
    def contains_any_keyword(text: str, keyword_list: List[str]) -> bool:
        """
        Returns True if any of the case-insensitive keywords is found in the text.
        """
        import re

        return any(
            re.search(re.escape(keyword), text, re.IGNORECASE)
            for keyword in keyword_list
        )

    @staticmethod
    def group_words_by_row(word_list: List[dict], margin: float) -> Dict[int, List[dict]]:
        """
        Groups words by their integer row index (word['top'] // margin).
        Useful for footer detection.
        """
        rows_by_key: Dict[int, List[dict]] = {}
        for word in word_list:
            key = int(word["top"] // margin)
            rows_by_key.setdefault(key, []).append(word)
        return rows_by_key

    @staticmethod
    def clean_dataframes(
        transaction_dataframe: pd.DataFrame, header_dataframe: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Standardizes column names, drops unused columns, cleans strings, and ensures no NaNs.
        ALWAYS uses .copy() when slicing to avoid SettingWithCopyWarning.
        """
        # ─── Clean header DataFrame ─────────────────────────────────
        if not header_dataframe.empty:
            header_dataframe = header_dataframe[
                [
                    "page_id",
                    "account_name",
                    "address",
                    "account_number",
                    "period",
                    "total_withdrawal_summary",
                    "total_deposit_summary",
                    "total_withdrawal_transaction_summary",
                    "total_deposit_transaction_summary",
                ]
            ].copy()

            header_dataframe["address"] = (
                header_dataframe["address"]
                .str.replace("\n", "", regex=False)
                .str.strip()
            )

            header_dataframe = header_dataframe.rename(
                columns={
                    "total_withdrawal_summary": "total_debit",
                    "total_deposit_summary": "total_credit",
                    "total_withdrawal_transaction_summary": "total_debit_transaction",
                    "total_deposit_transaction_summary": "total_credit_transaction",
                }
            )

            header_dataframe.fillna("", inplace=True)

            header_dataframe = header_dataframe[
                [
                    "page_id",
                    "account_name",
                    "account_number",
                    "period",
                    "total_debit",
                    "total_credit",
                    "total_debit_transaction",
                    "total_credit_transaction",
                    "address",
                ]
            ].copy()

        # ─── Clean transaction DataFrame ─────────────────────────────
        if not transaction_dataframe.empty:
            # Rename withdrawal/deposit → debit/credit
            transaction_dataframe = transaction_dataframe.rename(
                columns={"withdrawal": "debit", "deposit": "credit"}
            )

            # Convert debit/credit to numeric (float), coercing invalids to NaN
            transaction_dataframe["debit"] = pd.to_numeric(
                transaction_dataframe["debit"], errors="coerce"
            )
            transaction_dataframe["credit"] = pd.to_numeric(
                transaction_dataframe["credit"], errors="coerce"
            )
            # Convert balance to numeric (float), coercing invalids to NaN
            transaction_dataframe["balance"] = pd.to_numeric(
                transaction_dataframe["balance"], errors="coerce"
            )

            # Placeholder column (can be filled later if needed)
            transaction_dataframe["transaction_type"] = ""

        return transaction_dataframe, header_dataframe

    # ─── MAIN EXTRACTION LOGIC (STATIC) ─────────────────────────────────

    @staticmethod
    def extract_scb_data(
        pages: List[pdfplumber.page.Page]
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Expects a list of pdfplumber.page.Page objects (e.g., pages = pdf.pages).
        Iterates through each page:
          1. Extracts header fields via predefined bounding boxes.
          2. Finds table regions, splits into words, groups into rows, and parses each row.
        Returns two DataFrames: (transactions_df, headers_df).
        """
        transaction_records_list: List[Dict] = []
        header_records_list: List[Dict] = []

        for page_index, pdf_page in enumerate(pages):
            try:
                full_page_text = pdf_page.extract_text() or ""
                page_id_match = config.PAGE_ID_PATTERN.search(full_page_text)
                page_identifier = (
                    f"{page_id_match.group(1)}/{page_id_match.group(2)}"
                    if page_id_match
                    else None
                )

                # ─── HEADER EXTRACTION ──────────────────────────────────
                header_dict: Dict[str, Optional[object]] = {"page_id": page_identifier}
                has_credit_total = SCBStatementExtractor.contains_any_keyword(
                    full_page_text, ["TOTAL AMOUNTS (Credit)"]
                )

                for field_name, bounding_box in config.HEADER_CROP_REGIONS.items():
                    cropped_region = pdf_page.crop(bounding_box)
                    extracted_text = (cropped_region.extract_text() or "").strip()

                    if field_name.endswith("_summary"):
                        # Only capture summary if page actually has credit totals
                        if has_credit_total:
                            import re

                            money_match = re.search(
                                r"[\d,]+(?:\.\d{2})?", extracted_text
                            )
                            if money_match:
                                header_dict[field_name] = float(
                                    money_match.group().replace(",", "")
                                )
                            else:
                                header_dict[field_name] = None
                        else:
                            header_dict[field_name] = None
                    else:
                        header_dict[field_name] = extracted_text

                header_records_list.append(header_dict)

                # ─── TRANSACTION EXTRACTION ─────────────────────────────
                tables_on_page = pdf_page.find_tables(config.TABLE_SETTINGS)
                if tables_on_page:
                    regions_to_parse = [pdf_page.crop(t.bbox) for t in tables_on_page]
                else:
                    regions_to_parse = [pdf_page]

                for region in regions_to_parse:
                    word_list = region.extract_words(use_text_flow=True)

                    # Attempt footer removal (skip everything under "TOTAL AMOUNTS")
                    footer_y_coordinates = []
                    grouped_rows_for_footer = SCBStatementExtractor.group_words_by_row(
                        word_list, config.Y_MARGIN
                    )
                    for _, words_in_row in grouped_rows_for_footer.items():
                        if any("TOTAL AMOUNTS" in w["text"] for w in words_in_row):
                            minimal_y = min(w["top"] for w in words_in_row)
                            footer_y_coordinates.append(minimal_y)

                    if footer_y_coordinates:
                        cutoff_y = min(footer_y_coordinates) - config.Y_MARGIN
                        region_width = region.bbox[2]  # x1 coordinate = width
                        region = region.crop((0, 0, region_width, cutoff_y), relative=True)
                        word_list = region.extract_words(use_text_flow=True)

                    date_top_coordinates = SCBStatementExtractor.compute_date_top_coordinates(
                        word_list
                    )
                    if not date_top_coordinates:
                        continue  # No rows found here

                    row_intervals = SCBStatementExtractor.compute_row_intervals(
                        date_top_coordinates
                    )
                    rows_of_words = SCBStatementExtractor.assign_words_to_rows(
                        word_list, row_intervals
                    )

                    for single_row in rows_of_words:
                        if not single_row:
                            continue  # skip empty row

                        combined_row_text = " ".join(w["text"] for w in single_row)
                        if any(keyword in combined_row_text for keyword in ("TOTAL AMOUNTS", "TOTAL ITEMS")):
                            continue  # skip summary/footer rows

                        # Sort words top→bottom, then left→right
                        sorted_row = sorted(single_row, key=lambda w: (w["top"], w["x0"]))

                        # Extract date and time tokens
                        date_text = ""
                        time_text = ""
                        for word in sorted_row:
                            text_token = word["text"]
                            x0_coordinate = word["x0"]
                            if config.DATE_PATTERN.match(text_token) and config.DATE_X0 <= x0_coordinate <= config.DATE_X1:
                                date_text = text_token
                            elif config.TIME_PATTERN.match(text_token) and x0_coordinate > config.DATE_X1:
                                time_text = text_token

                        # Containers for code, channel, money words, balance, description
                        code_tokens: List[str] = []
                        channel_tokens: List[str] = []
                        debit_credit_word_objects: List[dict] = []
                        balance_word_objects: List[dict] = []
                        description_tokens: List[str] = []

                        # Populate containers by inspecting each word
                        for word in sorted_row:
                            text_token = word["text"]
                            x0_coordinate = word["x0"]

                            # Skip date/time tokens once captured
                            if config.DATE_PATTERN.match(text_token) or config.TIME_PATTERN.match(text_token):
                                continue

                            if config.MONEY_PATTERN.match(text_token):
                                # Monetary field: either debit/credit or balance
                                if x0_coordinate <= config.X_SPLIT_CHANNEL_DEBIT_CREDIT + config.X_TOLERANCE:
                                    debit_credit_word_objects.append(word)
                                elif x0_coordinate <= config.X_SPLIT_BALANCE_DESCRIPTION + config.X_TOLERANCE:
                                    balance_word_objects.append(word)
                                continue

                            if x0_coordinate <= config.X_SPLIT_CODE_CHANNEL + config.X_TOLERANCE:
                                code_tokens.append(text_token)
                            elif x0_coordinate <= config.X_SPLIT_CHANNEL_DEBIT_CREDIT + config.X_TOLERANCE:
                                channel_tokens.append(text_token)
                            else:
                                description_tokens.append(text_token)

                        # Convert debit/credit monetary words into numeric values
                        withdrawal_amount = None
                        deposit_amount = None
                        for money_word in debit_credit_word_objects:
                            numeric_value = float(money_word["text"].replace(",", ""))
                            if money_word["x1"] <= config.X_SPLIT_WITHDRAWAL_DEPOSIT + config.X_TOLERANCE:
                                withdrawal_amount = numeric_value
                            else:
                                deposit_amount = numeric_value

                        # Convert balance monetary words into a single float
                        balance_amount = None
                        for money_word in balance_word_objects:
                            if config.MONEY_PATTERN.match(money_word["text"]):
                                balance_amount = float(money_word["text"].replace(",", ""))
                                break

                        transaction_records_list.append(
                            {
                                "page_id": page_identifier,
                                "date": pd.to_datetime(
                                    date_text,
                                    format="%d/%m/%y",
                                    dayfirst=True,
                                    errors="coerce",
                                ),
                                "time": time_text,
                                "code": " ".join(code_tokens),
                                "channel": " ".join(channel_tokens),
                                "withdrawal": withdrawal_amount,
                                "deposit": deposit_amount,
                                "balance": balance_amount,
                                "description": " ".join(description_tokens),
                            }
                        )

            except Exception as extraction_error:
                # Print a warning but continue with the next page
                print(f"[Page {page_index + 1}] Extraction failed: {extraction_error}")

        # After iterating through all pages, build DataFrames:
        df_transactions = pd.DataFrame(transaction_records_list)
        df_headers = pd.DataFrame(header_records_list)

        # Clean and standardize before returning
        df_transactions_cleaned, df_headers_cleaned = SCBStatementExtractor.clean_dataframes(
            df_transactions, df_headers
        )
        return df_transactions_cleaned, df_headers_cleaned

    # ─── RUN METHOD (INSTANCE) ────────────────────────────────────────
    @staticmethod
    def run(pdf_path: str, password: Optional[str] = None
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Opens the PDF at `pdf_path` with optional `password`. Keeps it open
        while extracting, then closes it automatically. Returns:
          (transactions_df, headers_df)
        """
        with pdfplumber.open(pdf_path, password=password) as pdf:
            pages = pdf.pages
            return SCBStatementExtractor.extract_scb_data(pages)


# ─── USAGE EXAMPLE ─────────────────────────────────────────────────

#if __name__ == "__main__":
 #   extractor = SCBStatementExtractor()
  #  PDF_PATH = "/Users/if658228/Downloads/OneDrive_1_5-20-2025/agentic_extraction/Dataset04/SCB/no_note/108988-02031584-2566_1_SCB.pdf"
   # df_transactions, df_headers = extractor.run(PDF_PATH, password=PASSWORD)

    #print("Headers:")
    #print(df_headers.head())
    #print("\nTransactions:")
    #print(df_transactions.head())
