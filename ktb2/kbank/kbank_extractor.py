import re
from typing import Optional, List, Dict, Any, Tuple

import pdfplumber
import pandas as pd

import config  # Assumes config.py defines all constants (CROP_REGIONS, X_BOUNDS, tolerance_settings, TABLE_SETTINGS, DATE_PATTERN, TIME_PATTERN, MONEY_PATTERN, PAGE_ID_PATTERN, NUMERIC_CLEAN_PATTERN)


class KBANKStatementExtractor:
    """
    Class to extract header and transaction information from KBank PDF statements.
    All helper methods are @staticmethod. Only `run(pdf_path, password)` is used to execute.
    """

    @staticmethod
    def compute_date_tops(word_list: List[dict]) -> List[float]:
        """
        Identify y-coordinates ("top") of all words matching config.DATE_PATTERN within config.X_BOUNDS.
        Returns a sorted list of those y-coordinates.
        """
        return sorted(
            word["top"]
            for word in word_list
            if config.DATE_PATTERN.match(word["text"])
            and config.X_BOUNDS["date_min"] <= word["x0"] <= config.X_BOUNDS["date_max"]
        )

    @staticmethod
    def compute_intervals(date_top_list: List[float]) -> List[Tuple[float, float]]:
        """
        Given a sorted list of y-coordinates for dates, compute vertical intervals around each date.
        These intervals define “rows” in the transaction table.
        """
        interval_list: List[Tuple[float, float]] = []
        for index, top_value in enumerate(date_top_list):
            start_value = top_value - config.tolerance_settings["y_margin"]
            if index + 1 < len(date_top_list):
                end_value = date_top_list[index + 1] - config.tolerance_settings["y_margin"]
            else:
                if index > 0:
                    delta_value = top_value - date_top_list[index - 1]
                else:
                    delta_value = config.tolerance_settings["y_margin"] * 2
                end_value = top_value + delta_value - config.tolerance_settings["y_margin"]
            interval_list.append((start_value, end_value))
        return interval_list

    @staticmethod
    def assign_rows(word_list: List[dict], interval_list: List[Tuple[float, float]]) -> List[List[dict]]:
        """
        Assign each word to the first interval (row) whose vertical range contains that word’s top.
        Returns a list of lists, where each inner list contains the words for one row.
        """
        rows_for_each_interval: List[List[dict]] = [[] for _ in interval_list]
        for word in word_list:
            for interval_index, (start_value, end_value) in enumerate(interval_list):
                if start_value <= word["top"] < end_value:
                    rows_for_each_interval[interval_index].append(word)
                    break
        return rows_for_each_interval

    @staticmethod
    def extract_header(page) -> Dict[str, Optional[str]]:
        """
        Crop out header fields (account name, account number, period, totals, etc.) using config.CROP_REGIONS.
        If the "account_name" crop has two lines, the first line becomes account_name and the second line becomes address.
        Numeric totals are cleaned via config.NUMERIC_CLEAN_PATTERN.
        """
        header_data: Dict[str, Optional[str]] = {}

        for field_name, bounding_box in config.CROP_REGIONS.items():
            raw_text = page.crop(bounding_box).extract_text() or ""
            text_content = raw_text.strip()

            # Special handling: if "account_name" crop has two lines → first line is account_name, second line is address
            if field_name == "account_name":
                line_list = text_content.splitlines()
                header_data["account_name"] = line_list[0].strip() if line_list else ""
                if len(line_list) > 1:
                    header_data["address"] = line_list[1].strip()
                else:
                    header_data.setdefault("address", "")
                continue

            # If field_name == "address" but address was already set above, skip
            if field_name == "address" and header_data.get("address"):
                continue

            # Clean numeric totals for specific fields
            if field_name in {
                "total_withdrawal_transaction",
                "total_deposit_transaction",
                "total_withdrawal",
                "total_deposit",
                "ending_balance",
            }:
                match_object = config.NUMERIC_CLEAN_PATTERN.search(text_content)
                header_data[field_name] = match_object.group().replace(",", "") if match_object else None
            else:
                header_data[field_name] = text_content

        return header_data

    @staticmethod
    def parse_transaction_row(
        sorted_word_list: List[dict],
        page_identifier: str,
        account_address: str
    ) -> Optional[Dict[str, Any]]:
        """
        Given a sorted list of words representing one row, assign each word to:
          - date (config.DATE_PATTERN)
          - time (config.TIME_PATTERN)
          - withdrawal / deposit / balance (config.MONEY_PATTERN + x-position logic)
          - description_addon (if x between date_max and amount_desc_split)
          - channel (if x between amount_desc_split and channel_details_split)
          - detail description (everything else)
        Returns a dict with keys:
            {
              "page_id", "address", "date", "time", "description",
              "withdrawal", "deposit", "balance", "channel", "description_addon"
            }
        If no date is found in that row, returns None.
        """
        date_value = ""
        time_value = ""
        withdrawal_amount = None
        deposit_amount = None
        balance_amount = None
        description_addon_parts: List[str] = []
        channel_parts: List[str] = []
        detail_description_parts: List[str] = []

        for word in sorted_word_list:
            x_position = word["x0"]
            text_value = word["text"]
            x1_position = word["x1"]

            # 1) Date
            if (
                config.DATE_PATTERN.match(text_value)
                and config.X_BOUNDS["date_min"] <= x_position <= config.X_BOUNDS["date_max"]
            ):
                date_value = text_value

            # 2) Time
            elif config.TIME_PATTERN.match(text_value):
                time_value = text_value

            # 3) Money (withdrawal / deposit / balance)
            elif config.MONEY_PATTERN.match(text_value):
                numeric_value = float(text_value.replace(",", ""))
                if (
                    x_position <= config.X_BOUNDS["withdraw_deposit_split"] + config.tolerance_settings["x_tolerance"]
                    and x1_position <= config.X_BOUNDS["withdraw_deposit_split_x1"] + config.tolerance_settings["x_tolerance"]
                ):
                    withdrawal_amount = numeric_value
                elif x_position <= config.X_BOUNDS["amount_balance_split"] + config.tolerance_settings["x_tolerance"]:
                    deposit_amount = numeric_value
                else:
                    balance_amount = numeric_value

            # 4) description_addon if x just right of date_max but ≤ amount_desc_split
            elif (
                x_position > config.X_BOUNDS["date_max"] + config.tolerance_settings["x_tolerance"]
                and x_position <= config.X_BOUNDS["amount_desc_split"]
            ):
                description_addon_parts.append(text_value)

            # 5) channel if x just right of amount_desc_split but ≤ channel_details_split
            elif (
                x_position > config.X_BOUNDS["amount_desc_split"] + config.tolerance_settings["x_tolerance"]
                and x_position <= config.X_BOUNDS["channel_details_split"]
            ):
                channel_parts.append(text_value)

            # 6) Everything else → detail description
            else:
                detail_description_parts.append(text_value)

        if not date_value:
            return None

        return {
            "page_id": page_identifier,
            "address": account_address,
            "date": pd.to_datetime(date_value, format="%d-%m-%y", errors="coerce"),
            "time": time_value,
            "description": " ".join(detail_description_parts).strip(),
            "withdrawal": withdrawal_amount,
            "deposit": deposit_amount,
            "balance": balance_amount,
            "channel": " ".join(channel_parts).strip(),
            "description_addon": " ".join(description_addon_parts).strip()
        }

    @staticmethod
    def parse_page(page, table_settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Parse a single pdfplumber Page object:
          1) Extract header info via extract_header
          2) Determine page_id via config.PAGE_ID_PATTERN
          3) If “ENDING BALANCE” or “ยอดยกไป” not found, set header totals to None
          4) Find all table regions (or the entire page if none), group words into rows,
             and convert each row to a transaction dict via parse_transaction_row.
        Returns:
            - header_information: dict of header fields + page_id
            - transaction_record_list: list of dicts (one per valid transaction row)
        """
        # 1) Extract header fields
        header_information = KBANKStatementExtractor.extract_header(page)

        # 2) Clean page_id
        raw_page_id_value = header_information.get("page", "")
        page_id_match = config.PAGE_ID_PATTERN.search(raw_page_id_value)
        page_identifier = page_id_match.group(1) if page_id_match else raw_page_id_value
        header_information["page_id"] = page_identifier

        # 3) If no “ENDING BALANCE” or “ยอดยกไป” in the page text, null out header totals
        full_page_text = page.extract_text() or ""
        if not re.search(r"(ยอดยกไป|ENDING BALANCE)", full_page_text, re.IGNORECASE):
            for field_name in [
                "total_withdrawal_transaction",
                "total_deposit_transaction",
                "total_withdrawal",
                "total_deposit",
                "ending_balance"
            ]:
                header_information[field_name] = None

        # 4) Extract transaction rows
        transaction_record_list: List[Dict[str, Any]] = []
        table_list = page.find_tables(table_settings)
        region_list = [page.crop(table.bbox) for table in table_list] if table_list else [page]

        for region in region_list:
            word_list = region.extract_words(use_text_flow=True)
            date_top_list = KBANKStatementExtractor.compute_date_tops(word_list)
            if not date_top_list:
                continue

            interval_list = KBANKStatementExtractor.compute_intervals(date_top_list)
            rows_of_words = KBANKStatementExtractor.assign_rows(word_list, interval_list)

            for words_in_one_row in rows_of_words:
                if not words_in_one_row:
                    continue
                combined_row_text = " ".join(word["text"] for word in words_in_one_row)
                if "ENDING BALANCE" in combined_row_text or "ยอดยกไป" in combined_row_text:
                    continue

                sorted_word_list = sorted(words_in_one_row, key=lambda word: (word["top"], word["x0"]))
                transaction_record = KBANKStatementExtractor.parse_transaction_row(
                    sorted_word_list,
                    page_identifier,
                    header_information.get("address", "")
                )
                if transaction_record:
                    transaction_record_list.append(transaction_record)

        return header_information, transaction_record_list

    def extract_from_pages(self, page_list: List[pdfplumber.page.Page]) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Parse a list of pdfplumber Page objects (opened externally).
        Returns two DataFrames:
          - transactions_dataframe
          - headers_dataframe

        It does not open any PDF internally; you must pass in `page_list`.
        """
        header_data_list: List[Dict[str, Any]] = []
        transaction_data_list: List[Dict[str, Any]] = []

        for page_index, page in enumerate(page_list):
            try:
                header_information, transaction_record_list = KBANKStatementExtractor.parse_page(
                    page,
                    config.TABLE_SETTINGS
                )
                header_data_list.append(header_information)
                transaction_data_list.extend(transaction_record_list)
            except Exception as error:
                print(f"⚠️ Error on page {page_index + 1}: {error}")

        transactions_dataframe = pd.DataFrame(transaction_data_list).copy()
        headers_dataframe = pd.DataFrame(header_data_list).copy()

        if not transactions_dataframe.empty:
            # 1) Sum total withdrawal and total deposit per page
            totals_dataframe = (
                transactions_dataframe
                .groupby("page_id")[["withdrawal", "deposit"]]
                .sum()
                .rename(
                    columns={
                        "withdrawal": "total_withdrawal_each_page",
                        "deposit": "total_deposit_each_page"
                    }
                )
                .reset_index()
            )

            # 2) Count number of withdrawal and deposit transactions per page
            counts_dataframe = (
                transactions_dataframe
                .groupby("page_id")
                .agg(
                    total_withdrawal_transaction_each_page=pd.NamedAgg(
                        column="withdrawal", aggfunc=lambda series: series.notnull().sum()
                    ),
                    total_deposit_transaction_each_page=pd.NamedAgg(
                        column="deposit", aggfunc=lambda series: series.notnull().sum()
                    )
                )
                .reset_index()
            )

            # 3) Merge totals and counts back into transactions_dataframe and headers_dataframe
            transactions_dataframe = (
                transactions_dataframe
                .merge(totals_dataframe, on="page_id")
                .merge(counts_dataframe, on="page_id")
            )
            headers_dataframe = (
                headers_dataframe
                .merge(totals_dataframe, on="page_id", how="left")
                .merge(counts_dataframe, on="page_id", how="left")
            )

        # 4) Split the "period" field into start_period and end_period if present
        if "period" in headers_dataframe.columns:
            period_parts_dataframe = (
                headers_dataframe["period"]
                .str.replace(" ", "", regex=False)
                .str.split(r"[-–]", n=1, expand=True)
            )
            headers_dataframe["start_period"] = pd.to_datetime(
                period_parts_dataframe[0], dayfirst=True, errors="coerce"
            )
            if period_parts_dataframe.shape[1] > 1:
                headers_dataframe["end_period"] = pd.to_datetime(
                    period_parts_dataframe[1], dayfirst=True, errors="coerce"
                )
            else:
                headers_dataframe["end_period"] = None

        # 5) Convert numeric header columns to numeric dtype
        numeric_header_column_list = [
            "total_withdrawal",
            "total_deposit",
            "total_withdrawal_transaction",
            "total_deposit_transaction"
        ]
        for column_name in numeric_header_column_list:
            if column_name in headers_dataframe.columns:
                headers_dataframe[column_name] = pd.to_numeric(
                    headers_dataframe[column_name], errors="coerce"
                )

        return transactions_dataframe, headers_dataframe

    @staticmethod
    def clean_and_format_data(
        transactions_dataframe: pd.DataFrame,
        headers_dataframe: pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Take the raw DataFrames from extract_from_pages() and:
          1) Filter out rows without description or amount
          2) Select and rename columns to a standardized schema (withdrawal → debit, deposit → credit)
          3) Replace NaNs with empty strings
          4) From headers, select and rename total_* → total_debit / total_credit, etc.
          5) Drop header rows whose page_id does not start with a digit
          6) In transactions, add default "code" and "transaction_type" columns
          7) Convert numeric columns (debit, credit, balance, header totals) to float
        Returns:
          (final_transactions_dataframe, final_headers_dataframe)
        """
        # 1) Filter out any transaction rows missing description or amount
        filtered_transactions = (
            transactions_dataframe[
                ~(transactions_dataframe["description"].isnull())
                & (
                    (~transactions_dataframe["withdrawal"].isnull())
                    | (~transactions_dataframe["deposit"].isnull())
                )
            ]
            .copy()
        )

        # 2) Select and rename columns for transactions
        selected_transactions = filtered_transactions[
            [
                "page_id", "date", "time", "description", "withdrawal",
                "deposit", "balance", "channel", "description_addon"
            ]
        ].copy()
        renamed_transactions = selected_transactions.rename(
            columns={"withdrawal": "debit", "deposit": "credit"}
        )
        renamed_transactions.fillna("", inplace=True)

        # 3) Prepare and clean headers
        header_columns_to_select = [
            "page_id", "account_name", "account_number", "period",
            "total_withdrawal", "total_deposit",
            "total_withdrawal_transaction", "total_deposit_transaction",
            "address"
        ]
        available_header_columns = [
            column for column in header_columns_to_select if column in headers_dataframe.columns
        ]
        selected_headers = headers_dataframe[available_header_columns].copy()
        renamed_headers = selected_headers.rename(
            columns={
                "total_withdrawal": "total_debit",
                "total_deposit": "total_credit",
                "total_withdrawal_transaction": "total_debit_transaction",
                "total_deposit_transaction": "total_credit_transaction"
            }
        )
        filtered_headers = (
            renamed_headers[
                renamed_headers["page_id"].str.match(r"^\d", na=False)
            ]
            .reset_index(drop=True)
        )
        filtered_headers.fillna("", inplace=True)

        # 4) Enrich transaction DataFrame
        enriched_transactions = renamed_transactions.copy()
        enriched_transactions["code"] = None
        enriched_transactions["transaction_type"] = enriched_transactions["description_addon"]
        final_transactions = enriched_transactions[
            [
                "page_id", "date", "time", "code", "channel",
                "debit", "credit", "balance", "description", "transaction_type"
            ]
        ].copy()

        # 5) Clean numeric columns in transactions
        for column_name in ["debit", "credit", "balance"]:
            final_transactions[column_name] = (
                final_transactions[column_name]
                .replace(r"[^0-9\.]+", "", regex=True)
                .pipe(pd.to_numeric, errors="coerce")
            )

        # 6) Clean numeric columns in headers
        for column_name in [
            "total_debit", "total_credit",
            "total_debit_transaction", "total_credit_transaction"
        ]:
            if column_name in filtered_headers.columns:
                filtered_headers[column_name] = (
                    filtered_headers[column_name]
                    .replace(r"[^0-9\.]+", "", regex=True)
                    .pipe(pd.to_numeric, errors="coerce")
                )

        return final_transactions, filtered_headers
    
    @staticmethod
    def run(
        pdf_path: str,
        password: Optional[str] = None
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        with pdfplumber.open(pdf_path, password=password) as pdf_file:
            page_list = pdf_file.pages

            # Option B1: instantiate the extractor so you can call the instance method:
            extractor = KBANKStatementExtractor()
            raw_transactions_dataframe, raw_headers_dataframe = extractor.extract_from_pages(page_list)

            # Clean and format (note: clean_and_format_data is already @staticmethod)
            clean_transactions_dataframe, clean_headers_dataframe = KBANKStatementExtractor.clean_and_format_data(
                raw_transactions_dataframe,
                raw_headers_dataframe
            )

        return clean_transactions_dataframe, clean_headers_dataframe

