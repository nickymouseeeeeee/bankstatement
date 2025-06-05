import re  # Enables regex operations used throughout extraction and cleaning
from typing import Optional, List, Dict, Any  # Provides type hints
import pandas as pd  # Data manipulation library—used to build and clean DataFrames
import pdfplumber  # PDF parsing library—extracts text and table‐like structures from PDF pages
import dateutil.parser  # Flexible date parser—fallback when pandas date parsing fails

import config  # Import the entire config module

class GSBStatementExtractor:
    """
    Encapsulates all logic to extract header and transaction data from a GSB PDF bank statement.
    """

    @staticmethod
    def clean_page_id(raw_text: str) -> str:
        """
        Normalize a raw page-ID string (e.g., ' 1 / 10 ') to '1/10'; return empty if pattern fails.
        """
        numeric_parts = re.findall(r"\d+", raw_text)
        if len(numeric_parts) >= 2:
            candidate = f"{numeric_parts[0]}/{numeric_parts[1]}"
            if re.fullmatch(r"\d+/\d+", candidate):
                return candidate
        return ""

    @staticmethod
    def extract_account_number_and_period(full_text: str) -> tuple[str, str]:
        """
        Extract account number (9–12 digits) and statement period (“dd/mm/yyyy - dd/mm/yyyy”)
        from the entire page text using regex patterns.
        """
        account_match = config.ACCOUNT_NUMBER_PATTERN.search(full_text)
        period_match = config.PERIOD_PATTERN.search(full_text)
        account_number = account_match.group() if account_match else ""
        period_as_string = period_match.group() if period_match else ""
        return account_number, period_as_string

    @staticmethod
    def convert_be_to_ad(date_string: str) -> str:
        """
        Convert a date from Buddhist Era (BE) (year > 2400) to Gregorian (AD),
        e.g., “01/01/2567” → “01/01/2024”. If no BE year, return unchanged.
        """
        match = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", date_string)
        if match:
            day, month, year_str = match.groups()
            year = int(year_str)
            if year > 2400:
                year -= 543
            return f"{day}/{month}/{year}"
        return date_string

    @staticmethod
    def find_date_in_text(text: str) -> str:
        """
        If the text begins with a valid date (dd/mm/yy or dd/mm/yyyy), return that date;
        otherwise return an empty string.
        """
        match = re.match(r"^(\d{2}/\d{2}/(\d{4}|\d{4}))", text)
        return match.group(1) if match else ""

    @staticmethod
    def find_time_after_date_word(
        date_word: dict[str, Any], all_words: List[dict[str, Any]]
    ) -> str:
        """
        Given the date word dictionary and all words on the page,
        find a time (hh:mm) token that appears within 20 points below the date.
        """
        date_top = date_word.get("top", 0) if date_word else 0
        for word in all_words:
            if config.TIME_PATTERN.match(word["text"]) and 0 < (word["top"] - date_top) <= 20:
                return word["text"]
        return ""

    @staticmethod
    def extract_page_id(page: pdfplumber.page.Page) -> str:
        """
        Crop the page’s designated page‐ID area and normalize it using clean_page_id().
        """
        raw_crop_text = page.crop(config.PAGE_ID_CROP).extract_text() or ""
        return GSBStatementExtractor.clean_page_id(raw_crop_text.strip())

    @staticmethod
    def extract_transactions(pages: List[pdfplumber.page.Page]) -> List[Dict[str, Any]]:
        """
        For each page, crop to the transaction‐table region, group words into rows based on date positions,
        and assemble structured transaction records.
        """
        transaction_records: List[Dict[str, Any]] = []

        for page_index, page in enumerate(pages, start=1):
            try:
                page_id = GSBStatementExtractor.extract_page_id(page)
                table_region = page.crop(config.TABLE_CROP_BOX)
                all_words = table_region.extract_words(use_text_flow=False)

                date_tops = sorted(
                    w["top"] for w in all_words if GSBStatementExtractor.find_date_in_text(w["text"])
                )
                if not date_tops:
                    continue

                row_intervals: List[tuple[float, float]] = []
                for idx, top_y in enumerate(date_tops):
                    start_y = top_y - config.Y_MARGIN
                    end_y = (
                        date_tops[idx + 1] - config.Y_MARGIN if idx + 1 < len(date_tops) else top_y + 15
                    )
                    row_intervals.append((start_y, end_y))

                rows: List[List[dict[str, Any]]] = [[] for _ in row_intervals]
                for word in all_words:
                    for row_idx, (start_y, end_y) in enumerate(row_intervals):
                        if start_y <= word["top"] < end_y:
                            rows[row_idx].append(word)
                            break

                for row_words in rows:
                    if not row_words:
                        continue

                    sorted_row = sorted(row_words, key=lambda w: (w["top"], w["x0"]))
                    first_word = next((w for w in sorted_row if w["text"].strip()), None)
                    if not first_word or not GSBStatementExtractor.find_date_in_text(first_word["text"]):
                        continue

                    date_word = first_word
                    date_str_raw = GSBStatementExtractor.find_date_in_text(date_word["text"])
                    date_str_converted = GSBStatementExtractor.convert_be_to_ad(date_str_raw)

                    try:
                        date_value = pd.to_datetime(date_str_converted, dayfirst=True, errors="raise")
                    except Exception:
                        try:
                            date_value = dateutil.parser.parse(date_str_converted, dayfirst=True, fuzzy=True)
                        except Exception:
                            date_value = pd.NaT

                    date_remainder = ""
                    if date_word and date_str_raw:
                        date_length = len(date_str_raw)
                        date_remainder = date_word["text"][date_length:]

                    cleaned_row_words: List[dict[str, Any]] = []
                    for word in sorted_row:
                        if word is date_word and date_remainder:
                            cleaned_row_words.append({
                                "text": date_remainder,
                                "x0": word["x0"],
                                "x1": word["x1"],
                                "top": word["top"]
                            })
                            continue
                        if word is date_word:
                            continue
                        cleaned_row_words.append(word)

                    code_tokens: List[str] = []
                    channel_tokens: List[str] = []
                    description_tokens: List[str] = []

                    for word in cleaned_row_words:
                        text_value = word["text"]
                        x0 = word["x0"]
                        if not text_value.strip():
                            continue
                        if config.TIME_PATTERN.match(text_value):
                            continue
                        if config.MONEY_PATTERN.match(text_value):
                            continue
                        if x0 <= config.SPLIT_X_CODE_CHANNEL + config.X_TOLERANCE:
                            code_tokens.append(text_value)
                        elif x0 <= config.SPLIT_X_CHANNEL_DEBIT_CREDIT + config.X_TOLERANCE:
                            channel_tokens.append(text_value)
                        else:
                            description_tokens.append(text_value)

                    full_code_channel = "/".join(code_tokens + channel_tokens)
                    parts = full_code_channel.split("/", 1)
                    code_value = parts[0]
                    channel_value = parts[1] if len(parts) > 1 else ""

                    money_words: List[Dict[str, Any]] = []
                    for word in cleaned_row_words:
                        text_value = word["text"]
                        if config.MONEY_PATTERN.match(text_value):
                            raw = text_value.replace(",", "").strip()
                            is_negative = False

                            if raw.startswith("(") and raw.endswith(")"):
                                is_negative = True
                                raw = raw[1:-1]

                            try:
                                val = float(raw)
                                if is_negative:
                                    val = -val
                            except ValueError:
                                val = None

                            if val is not None:
                                money_words.append({
                                    "value": val,
                                    "x1": word["x1"]
                                })

                    money_words_sorted = sorted(money_words, key=lambda w: w["x1"])

                    withdrawal_amount: float | None = None
                    deposit_amount: float | None = None
                    balance_value: float | None = None

                    if len(money_words_sorted) == 1:
                        only = money_words_sorted[0]
                        if only["x1"] <= config.SPLIT_X_WITHDRAWAL_DEPOSIT + config.X_TOLERANCE:
                            withdrawal_amount = only["value"]
                        else:
                            deposit_amount = only["value"]
                    elif len(money_words_sorted) >= 2:
                        first = money_words_sorted[0]
                        if first["x1"] <= config.SPLIT_X_WITHDRAWAL_DEPOSIT + config.X_TOLERANCE:
                            withdrawal_amount = first["value"]
                            if len(money_words_sorted) == 2:
                                balance_value = money_words_sorted[1]["value"]
                            else:
                                deposit_amount = money_words_sorted[1]["value"]
                                if len(money_words_sorted) >= 3:
                                    balance_value = money_words_sorted[2]["value"]
                        else:
                            deposit_amount = first["value"]
                            if len(money_words_sorted) == 2:
                                balance_value = money_words_sorted[1]["value"]
                            else:
                                balance_value = money_words_sorted[1]["value"]

                    record = {
                        "page_id": page_id,
                        "date": date_value,
                        "time": GSBStatementExtractor.find_time_after_date_word(date_word, all_words) if date_word else "",
                        "code": code_value,
                        "channel": channel_value,
                        "withdrawal": withdrawal_amount,
                        "deposit": deposit_amount,
                        "balance": balance_value,
                        "description": code_value + " " + channel_value
                    }

                    if page_id == "":
                        record = {key: "" for key in record}

                    transaction_records.append(record)

            except Exception as extraction_error:
                print(f"⚠️ Skipping page {page_index} in transaction extraction due to error: {extraction_error}")
                continue

        return transaction_records

    @staticmethod
    def extract_headers(pages: List[pdfplumber.page.Page]) -> List[Dict[str, Any]]:
        """
        Iterate through each page and extract header information (account number, period,
        account_name crop, plus footer totals if present).
        """
        header_rows: List[Dict[str, Any]] = []

        for page_index, page in enumerate(pages, start=1):
            try:
                page_id = GSBStatementExtractor.extract_page_id(page)
                full_text = page.extract_text() or ""
                has_footer_summary = any(
                    keyword.lower() in full_text.lower() for keyword in config.FOOTER_KEYWORDS
                )

                account_number, period_as_string = GSBStatementExtractor.extract_account_number_and_period(full_text)

                header_data: Dict[str, Any] = {
                    "page_id": page_id,
                    "account_number": account_number,
                    "period": period_as_string,
                }

                for field_name, bounding_box in config.CROPS.items():
                    raw_text = page.crop(bounding_box).extract_text() or ""
                    header_data[field_name] = raw_text.strip().replace("\n", " ")

                if has_footer_summary:
                    for line in full_text.splitlines():
                        if line.startswith("ยอดรวมรายการถอน") or line.startswith("Total Withdrawal"):
                            numbers = re.findall(r"[\d,]+(?:\.\d{2})?", line)
                            header_data.update({
                                "total_items_debit": numbers[0].replace(",", "") if len(numbers) > 0 else None,
                                "total_amount_debit": numbers[1].replace(",", "") if len(numbers) > 1 else None
                            })
                        elif line.startswith("ยอดรวมรายการฝาก") or line.startswith("Total Deposit"):
                            numbers = re.findall(r"[\d,]+(?:\.\d{2})?", line)
                            header_data.update({
                                "total_items_credit": numbers[0].replace(",", "") if len(numbers) > 0 else None,
                                "total_amount_credit": numbers[1].replace(",", "") if len(numbers) > 1 else None
                            })

                if page_id == "":
                    header_data = {key: "" for key in header_data}

                header_rows.append(header_data)

            except Exception as header_error:
                print(f"⚠️ Skipping page {page_index} in header extraction due to error: {header_error}")
                continue

        return header_rows

    @staticmethod
    def _clean_float_column(series: pd.Series) -> pd.Series:
        """
        Given a pandas Series of strings like '-1,234.56', '(1,234.56)' or '1234.56',
        strip out commas and parentheses, but keep a leading minus, then convert to float.
        """
        def parse_money(s: str) -> float:
            s = str(s).strip()
            if not s:
                return float("nan")
            is_neg = False

            if s.startswith("(") and s.endswith(")"):
                is_neg = True
                s = s[1:-1]

            cleaned = re.sub(r"[^0-9\.\-]", "", s)
            try:
                num = float(cleaned)
            except ValueError:
                return float("nan")
            return -num if is_neg else num

        return series.astype(str).apply(parse_money)

    @staticmethod
    def clean_dataframes(
        transactions_df: pd.DataFrame,
        headers_df: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Rename, filter, and convert columns for both header and transaction DataFrames.
        Uses .copy() after any slicing to prevent SettingWithCopyWarning.
        """
        headers_df = headers_df.rename(columns={
            "total_amount_debit": "total_debit",
            "total_amount_credit": "total_credit",
            "total_items_debit": "total_debit_transaction",
            "total_items_credit": "total_credit_transaction"
        })

        headers_df = headers_df.fillna("").copy()
        headers_df["address"] = ""
        headers_df = headers_df[headers_df["page_id"].str.match(r"^\d", na=False)].copy().reset_index(drop=True)

        transactions_df = transactions_df.rename(columns={"withdrawal": "debit", "deposit": "credit"})
        transactions_df = transactions_df[
            ~(transactions_df['balance'].isnull()) & (
                ~(transactions_df['debit'].isnull()) | ~(transactions_df['credit'].isnull())
            )
        ]
        transactions_df = transactions_df.fillna("").copy()
        transactions_df["transaction_type"] = ""
        
        for col_name in ["debit", "credit", "balance"]:
            if col_name in transactions_df.columns:
                transactions_df[col_name] = GSBStatementExtractor._clean_float_column(transactions_df[col_name])

        for col_name in [
            "total_debit", "total_credit",
            "total_debit_transaction", "total_credit_transaction"
        ]:
            if col_name in headers_df.columns:
                headers_df[col_name] = GSBStatementExtractor._clean_float_column(headers_df[col_name])

        return headers_df, transactions_df
    @staticmethod
    def run(
        pdf_path: str,
        password: Optional[str]
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Main entry point: open the PDF at the given path with the given password,
        extract raw header and transaction records, convert to DataFrames, clean them,
        print summaries, and return both DataFrames.
        """
        with pdfplumber.open(pdf_path, password=password) as pdf_handle:
            pages = pdf_handle.pages
            raw_transaction_records = GSBStatementExtractor.extract_transactions(pages)
            raw_header_records = GSBStatementExtractor.extract_headers(pages)

        transaction_dataframe = pd.DataFrame(raw_transaction_records)
        header_dataframe = pd.DataFrame(raw_header_records)

        cleaned_header_df, cleaned_transaction_df = GSBStatementExtractor.clean_dataframes(
            transaction_dataframe, header_dataframe
        )

        print("=== Header DataFrame ===")
        print(cleaned_header_df.to_string(index=False))
        print("\n=== Last 10 Transactions ===")
        print(cleaned_transaction_df.tail(10).to_string(index=False))

        return cleaned_header_df, cleaned_transaction_df


