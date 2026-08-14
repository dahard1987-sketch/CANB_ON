from __future__ import annotations

import argparse
import http.client
import os
import random
import re
import socket
import ssl
import time
import unicodedata
import warnings
from collections import Counter
from datetime import (
    date,
    datetime,
    time as datetime_time,
    timedelta,
    timezone,
)
from pathlib import Path
from typing import Any, Callable

import httplib2
import google.auth
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openpyxl import load_workbook


# ============================================================
# 설정
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

# Excel 파일들이 들어 있는 폴더
SOURCE_DIR = Path.home() / "Desktop" / "온라인 업데이트용"

# 이 파이썬 파일과 같은 폴더에 있는 서비스 계정 JSON 파일
_service_account_setting = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "service-account.json",
)
SERVICE_ACCOUNT_FILE = Path(_service_account_setting)
if not SERVICE_ACCOUNT_FILE.is_absolute():
    SERVICE_ACCOUNT_FILE = PROJECT_DIR / SERVICE_ACCOUNT_FILE

# 업데이트할 Google 스프레드시트 ID
SPREADSHEET_ID = os.environ.get(
    "GOOGLE_SPREADSHEET_ID",
    "",
).strip()

# 각 Excel 파일에서 읽을 시트 이름
SOURCE_SHEET_NAME = "학생 상세"

# Excel에서 읽을 범위: A:K
MAX_COLUMN = 11

# G열에서 찾을 수 있는 레벨과 대상 Google Sheets 탭 이름
SUPPORTED_LEVELS = (
    "Hexa 1",
    "Hexa 2",
    "Hepta 1",
    "Hepta 2",
    "Octa 1",
    "Octa 2",
    "Octa 3",
    "Nona 1",
    "Nona 2",
    "Nona 3",
)

LEVEL_PATTERN = re.compile(
    r"(?<![a-z])(hexa|hepta|octa|nona)\s*([1-3])(?!\d)",
    re.IGNORECASE,
)

# Google Sheets API에 한 번에 전송할 최대 행 수
CHUNK_SIZE = 2000

# 최초 시도 실패 후 추가로 재시도할 횟수
MAX_RETRIES = 5

# 재시도 대기 시간
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0

# 한국 시간대
KOREA_TIMEZONE = timezone(
    timedelta(hours=9)
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# 값 읽기와 무관한 openpyxl 기본 스타일 경고 숨기기
warnings.filterwarnings(
    "ignore",
    message="Workbook contains no default style.*",
    category=UserWarning,
)

# 자동 재시도할 HTTP 상태 코드
RETRYABLE_HTTP_STATUSES = {
    408,
    429,
    500,
    502,
    503,
    504,
}

# 자동 재시도할 네트워크 예외
# WinError 10054도 OSError 계열이므로 포함됩니다.
RETRYABLE_NETWORK_ERRORS = (
    OSError,
    TimeoutError,
    ConnectionError,
    socket.timeout,
    ssl.SSLError,
    http.client.HTTPException,
    httplib2.HttpLib2Error,
)


# ============================================================
# 명령줄 옵션
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "온라인 업데이트용 폴더의 Excel 데이터를 "
            "Google Sheets로 업데이트합니다."
        )
    )

    parser.add_argument(
        "-o",
        "--only",
        nargs="+",
        metavar="파일명",
        help=(
            "지정한 Excel 파일만 업데이트합니다. "
            "확장자는 생략할 수 있습니다. "
            '예: -o "Nona 1" "Octa 1" 또는 '
            '-o "Nona 1; Octa 1"'
        ),
    )

    return parser.parse_args()


def normalize_file_key(name: str) -> str:
    """
    파일명을 비교할 때 대소문자와 중복 공백을 무시합니다.
    """
    return " ".join(
        name.strip().split()
    ).casefold()


def parse_requested_targets(
    arguments: list[str] | None,
) -> list[str] | None:
    """
    다음 입력 방식을 모두 처리합니다.

    -o "Nona 1" "Octa 1"
    -o "Nona 1; Octa 1"
    """
    if not arguments:
        return None

    targets: list[str] = []
    seen_keys: set[str] = set()

    for argument in arguments:
        for part in argument.split(";"):
            cleaned = part.strip()

            if not cleaned:
                continue

            # Nona 1.xlsx라고 입력해도 Nona 1로 처리
            target_name = Path(cleaned).stem
            target_key = normalize_file_key(
                target_name
            )

            if target_key not in seen_keys:
                targets.append(target_name)
                seen_keys.add(target_key)

    return targets or None


def select_excel_files(
    all_excel_files: list[Path],
    requested_targets: list[str] | None,
) -> tuple[list[Path], list[str]]:
    """
    전체 파일 중 사용자가 지정한 파일만 선택합니다.
    """
    if requested_targets is None:
        return all_excel_files, []

    available_files = {
        normalize_file_key(file_path.stem): file_path
        for file_path in all_excel_files
    }

    selected_files: list[Path] = []
    missing_targets: list[str] = []

    for target_name in requested_targets:
        file_path = available_files.get(
            normalize_file_key(target_name)
        )

        if file_path is None:
            missing_targets.append(
                target_name
            )
            continue

        if file_path not in selected_files:
            selected_files.append(
                file_path
            )

    return selected_files, missing_targets


# ============================================================
# 자동 재시도
# ============================================================

def is_retryable_error(
    error: Exception,
) -> bool:
    """
    일시적인 연결 오류인지 확인합니다.
    """
    if isinstance(error, HttpError):
        response = getattr(
            error,
            "resp",
            None,
        )

        status = getattr(
            response,
            "status",
            None,
        )

        return (
            status
            in RETRYABLE_HTTP_STATUSES
        )

    return isinstance(
        error,
        RETRYABLE_NETWORK_ERRORS,
    )


def run_with_retry(
    operation: Callable[[], Any],
    description: str,
) -> Any:
    """
    연결이 끊기거나 Google API가 일시적으로 실패하면
    대기 시간을 늘려 가며 자동 재시도합니다.
    """
    for retry_index in range(
        MAX_RETRIES + 1
    ):
        try:
            return operation()

        except Exception as error:
            if not is_retryable_error(error):
                raise

            if retry_index >= MAX_RETRIES:
                print(
                    f"  {description}: "
                    f"자동 재시도 {MAX_RETRIES}회를 "
                    "모두 실패했습니다."
                )
                raise

            wait_seconds = min(
                RETRY_BASE_DELAY
                * (2 ** retry_index)
                + random.uniform(
                    0.0,
                    0.5,
                ),
                RETRY_MAX_DELAY,
            )

            if isinstance(error, HttpError):
                response = getattr(
                    error,
                    "resp",
                    None,
                )

                status = getattr(
                    response,
                    "status",
                    "?",
                )

                error_label = (
                    f"HTTP {status} 오류"
                )

            else:
                error_label = (
                    f"연결 오류: {error}"
                )

            print(
                f"  {description} 중 "
                f"{error_label}"
            )

            print(
                f"  {wait_seconds:.1f}초 후 "
                f"자동 재시도 "
                f"({retry_index + 1}/{MAX_RETRIES})"
            )

            time.sleep(
                wait_seconds
            )

    raise RuntimeError(
        f"{description}: "
        "알 수 없는 재시도 오류가 발생했습니다."
    )


# ============================================================
# 공통 함수
# ============================================================

def normalize_value(
    value: Any,
) -> Any:
    """
    Excel 값을 Google Sheets API가 받을 수 있는
    형태로 변환합니다.
    """
    if value is None:
        return ""

    if isinstance(value, datetime):
        if value.time() == datetime_time(
            0,
            0,
        ):
            return value.strftime(
                "%Y-%m-%d"
            )

        return value.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    if isinstance(value, date):
        return value.strftime(
            "%Y-%m-%d"
        )

    if isinstance(
        value,
        datetime_time,
    ):
        return value.strftime(
            "%H:%M:%S"
        )

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    return str(value)


def row_is_empty(
    row: list[Any],
) -> bool:
    """
    행 전체가 빈 값인지 확인합니다.
    """
    return all(
        value is None
        or value == ""
        for value in row
    )


def quote_sheet_name(
    sheet_name: str,
) -> str:
    """
    공백이나 작은따옴표가 있는 시트 이름을
    Google Sheets 범위에 안전하게 사용합니다.
    """
    escaped_name = (
        sheet_name.replace(
            "'",
            "''",
        )
    )

    return f"'{escaped_name}'"


def make_update_timestamp() -> str:
    """
    기존 GAS와 같은 형식의
    최종 업데이트 문구를 만듭니다.
    """
    now = datetime.now(
        KOREA_TIMEZONE
    )

    days = [
        "월",
        "화",
        "수",
        "목",
        "금",
        "토",
        "일",
    ]

    day = days[
        now.weekday()
    ]

    return (
        "📌 최종 업데이트: "
        f"{now.month}/{now.day}({day}) "
        f"{now.hour:02d}:{now.minute:02d}"
    )


# ============================================================
# Excel 읽기
# ============================================================

def normalize_level_value(value: Any) -> str | None:
    """G열의 값을 지원하는 표준 레벨명으로 변환합니다."""
    if value is None:
        return None

    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = LEVEL_PATTERN.search(text)

    if not match:
        return None

    family = match.group(1).capitalize()
    normalized = f"{family} {match.group(2)}"
    return normalized if normalized in SUPPORTED_LEVELS else None


def detect_level_from_worksheet(worksheet) -> str:
    """
    G열 전체에서 레벨을 찾습니다.

    같은 레벨이 여러 행에 반복되는 것은 허용하지만, 서로 다른 레벨이
    함께 있으면 어느 탭을 갱신해야 할지 모호하므로 거부합니다.
    """
    detected_levels = [
        level
        for (value,) in worksheet.iter_rows(
            min_col=7,
            max_col=7,
            values_only=True,
        )
        if (level := normalize_level_value(value)) is not None
    ]

    if not detected_levels:
        raise ValueError(
            "G열에서 지원하는 레벨을 찾지 못했습니다."
        )

    counts = Counter(detected_levels)
    if len(counts) > 1:
        ranked = counts.most_common()
        primary_level, primary_count = ranked[0]
        second_count = ranked[1][1]
        if primary_count > second_count and primary_count / len(detected_levels) >= 0.8:
            return primary_level

        found = ", ".join(sorted(counts))
        raise ValueError(
            f"G열의 레벨을 하나로 확인할 수 없습니다: {found}"
        )

    return next(iter(counts))


def inspect_excel_file(
    file_path: Path,
) -> tuple[str, list[list[Any]]]:
    """학생 상세 시트의 G열 레벨과 A:K 데이터를 한 번에 읽습니다."""
    workbook = load_workbook(
        filename=file_path,
        read_only=True,
        data_only=True,
    )

    try:
        if SOURCE_SHEET_NAME not in workbook.sheetnames:
            current_sheets = ", ".join(workbook.sheetnames)
            raise ValueError(
                f"'{SOURCE_SHEET_NAME}' 시트가 없습니다. "
                f"현재 시트: {current_sheets}"
            )

        worksheet = workbook[SOURCE_SHEET_NAME]
        level = detect_level_from_worksheet(worksheet)
        rows = [
            list(excel_row)
            for excel_row in worksheet.iter_rows(
                min_row=1,
                min_col=1,
                max_col=MAX_COLUMN,
                values_only=True,
            )
        ]

        while rows and row_is_empty(rows[-1]):
            rows.pop()

        values = [
            [normalize_value(value) for value in row]
            for row in rows
        ]
        return level, values

    finally:
        workbook.close()

def read_excel_file(
    file_path: Path,
) -> list[list[Any]]:
    """
    Excel 파일의 '학생 상세' 시트에서
    A열부터 K열까지 읽습니다.
    """
    _, values = inspect_excel_file(file_path)
    return values


# ============================================================
# Google Sheets 연결
# ============================================================

def create_sheets_service():
    """
    서비스 계정 키 또는 플랫폼 기본 인증(ADC)으로 연결합니다.
    """
    if os.environ.get("GOOGLE_USE_ADC") == "1":
        credentials, _ = google.auth.default(
            scopes=SCOPES,
        )
    else:
        if not SERVICE_ACCOUNT_FILE.exists():
            raise FileNotFoundError(
                "서비스 계정 파일이 없습니다:\n"
                f"{SERVICE_ACCOUNT_FILE}\n"
                "Cloud Run 등에서는 GOOGLE_USE_ADC=1로 설정할 수 있습니다."
            )

        credentials = (
            Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE,
                scopes=SCOPES,
            )
        )

    return build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )


def get_sheet_names(
    service,
) -> set[str]:
    """
    구글 스프레드시트의 탭 이름들을 가져옵니다.
    """

    if not SPREADSHEET_ID:
        raise ValueError(
            "GOOGLE_SPREADSHEET_ID 환경 변수가 설정되지 않았습니다."
        )

    def request_sheet_names():
        return (
            service.spreadsheets()
            .get(
                spreadsheetId=SPREADSHEET_ID,
                fields=(
                    "sheets.properties.title"
                ),
            )
            .execute()
        )

    result = run_with_retry(
        request_sheet_names,
        "구글 시트 탭 목록 확인",
    )

    return {
        sheet["properties"]["title"]
        for sheet in result.get(
            "sheets",
            [],
        )
    }


# ============================================================
# Google Sheets 쓰기
# ============================================================

def clear_target_sheet(
    service,
    sheet_name: str,
) -> None:
    """
    대상 탭의 A:K 기존 값만 삭제합니다.
    서식과 L열 이후 값은 건드리지 않습니다.
    """
    target_range = (
        f"{quote_sheet_name(sheet_name)}!A:K"
    )

    def clear_request():
        return (
            service.spreadsheets()
            .values()
            .clear(
                spreadsheetId=SPREADSHEET_ID,
                range=target_range,
                body={},
            )
            .execute()
        )

    run_with_retry(
        clear_request,
        f"{sheet_name} 기존 데이터 삭제",
    )


def upload_values(
    service,
    sheet_name: str,
    values: list[list[Any]],
) -> int:
    """
    데이터를 CHUNK_SIZE 단위로 나누어 업로드합니다.
    """
    updated_cells = 0

    for start_index in range(
        0,
        len(values),
        CHUNK_SIZE,
    ):
        chunk = values[
            start_index:
            start_index + CHUNK_SIZE
        ]

        start_row = (
            start_index + 1
        )

        end_row = (
            start_row
            + len(chunk)
            - 1
        )

        target_range = (
            f"{quote_sheet_name(sheet_name)}!"
            f"A{start_row}:K{end_row}"
        )

        def upload_chunk(
            current_chunk: list[list[Any]] = chunk,
            current_range: str = target_range,
        ) -> dict[str, Any]:
            return (
                service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=current_range,
                    valueInputOption="RAW",
                    body={
                        "majorDimension": "ROWS",
                        "values": current_chunk,
                    },
                )
                .execute()
            )

        result = run_with_retry(
            upload_chunk,
            (
                f"{sheet_name} "
                f"{start_row:,}~{end_row:,}행 업로드"
            ),
        )

        updated_cells += result.get(
            "updatedCells",
            0,
        )

    return updated_cells


def update_display_timestamp(
    service,
    source_sheet_name: str,
    existing_sheet_names: set[str],
) -> bool:
    """
    예:
    Nona 1 업데이트 완료
    → Nona 1_표시 탭 A1에 최종 업데이트 시간 기록
    """
    display_sheet_name = (
        f"{source_sheet_name}_표시"
    )

    if (
        display_sheet_name
        not in existing_sheet_names
    ):
        print(
            f"  알림: '{display_sheet_name}' 탭이 없어 "
            "업데이트 시간은 기록하지 않았습니다."
        )

        return False

    timestamp_text = (
        make_update_timestamp()
    )

    target_range = (
        f"{quote_sheet_name(display_sheet_name)}!A1"
    )

    def timestamp_request():
        return (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=SPREADSHEET_ID,
                range=target_range,
                valueInputOption="RAW",
                body={
                    "majorDimension": "ROWS",
                    "values": [
                        [timestamp_text]
                    ],
                },
            )
            .execute()
        )

    run_with_retry(
        timestamp_request,
        (
            f"{display_sheet_name} "
            "업데이트 시간 기록"
        ),
    )

    print(
        f"  업데이트 시간: "
        f"{timestamp_text}"
    )

    return True


# ============================================================
# 메인 실행
# ============================================================

def main() -> None:
    args = parse_args()

    requested_targets = (
        parse_requested_targets(
            args.only
        )
    )

    if not SOURCE_DIR.exists():
        raise FileNotFoundError(
            "원본 폴더가 없습니다:\n"
            f"{SOURCE_DIR}"
        )

    all_excel_files = sorted(
        file_path
        for file_path
        in SOURCE_DIR.glob("*.xlsx")
        if not file_path.name.startswith("~$")
    )

    if not all_excel_files:
        raise FileNotFoundError(
            "폴더 안에 .xlsx 파일이 없습니다:\n"
            f"{SOURCE_DIR}"
        )

    excel_files, missing_targets = (
        select_excel_files(
            all_excel_files,
            requested_targets,
        )
    )

    if missing_targets:
        print(
            "경고: 원본 폴더에서 찾지 못한 파일: "
            + ", ".join(
                missing_targets
            )
        )

    if not excel_files:
        available_names = ", ".join(
            file_path.stem
            for file_path
            in all_excel_files
        )

        raise FileNotFoundError(
            "선택 조건에 맞는 "
            ".xlsx 파일이 없습니다.\n"
            "현재 사용 가능한 파일: "
            f"{available_names}"
        )

    service = create_sheets_service()

    existing_sheet_names = (
        get_sheet_names(service)
    )

    print("=" * 60)
    print("온라인 업데이트 시작")
    print(
        f"원본 폴더: {SOURCE_DIR}"
    )

    if requested_targets is None:
        print(
            "실행 모드: 전체 파일"
        )

    else:
        print(
            "실행 모드: 선택한 파일만"
        )

        print(
            "선택 파일: "
            + ", ".join(
                file_path.stem
                for file_path
                in excel_files
            )
        )

    print(
        f"처리할 파일: "
        f"{len(excel_files)}개"
    )

    print("=" * 60)

    success_count = 0
    failure_count = 0

    for file_path in excel_files:
        print(
            f"\n[{file_path.name}]"
        )

        try:
            # Excel 파일을 정상적으로 읽은 뒤에만
            # 구글 시트의 기존 데이터를 삭제합니다.
            target_sheet_name, values = inspect_excel_file(
                file_path
            )

            if target_sheet_name not in existing_sheet_names:
                print(
                    "  실패: 구글 스프레드시트에 "
                    f"'{target_sheet_name}' 탭이 없습니다."
                )
                failure_count += 1
                continue

            if not values:
                print(
                    "  실패: '학생 상세' 시트의 "
                    "A:K에 데이터가 없습니다."
                )

                failure_count += 1
                continue

            clear_target_sheet(
                service,
                target_sheet_name,
            )

            updated_cells = upload_values(
                service,
                target_sheet_name,
                values,
            )

            # 데이터 업로드가 완료된 뒤
            # 같은 이름의 '_표시' 탭 A1에 시간 기록
            update_display_timestamp(
                service,
                target_sheet_name,
                existing_sheet_names,
            )

            print(
                f"  대상 탭: "
                f"{target_sheet_name}"
            )

            print(
                f"  복사 범위: "
                f"A1:K{len(values)}"
            )

            print(
                f"  행 수: "
                f"{len(values):,}"
            )

            print(
                f"  업데이트 셀 수: "
                f"{updated_cells:,}"
            )

            print("  완료")

            success_count += 1

            # API 요청이 너무 연속되지 않도록 잠시 대기
            time.sleep(1)

        except Exception as error:
            print(
                f"  실패: {error}"
            )

            failure_count += 1

    print(
        "\n" + "=" * 60
    )

    print("전체 작업 종료")

    print(
        f"성공: {success_count}개"
    )

    print(
        f"실패: {failure_count}개"
    )

    print("=" * 60)


if __name__ == "__main__":
    try:
        main()

    except HttpError as error:
        print(
            "\nGoogle Sheets API 오류가 "
            "발생했습니다."
        )

        print(error)

        response = getattr(
            error,
            "resp",
            None,
        )

        if (
            response is not None
            and response.status == 403
        ):
            print(
                "\n확인 사항:"
                "\n1. Google Sheets API가 "
                "활성화되어 있는지"
                "\n2. service-account.json의 "
                "client_email을 대상 "
                "스프레드시트에 편집자로 공유했는지"
            )

    except Exception as error:
        print(
            "\n실행 중 오류가 발생했습니다."
        )

        print(error)

    input(
        "\nEnter 키를 누르면 종료합니다."
    )


# ============================================================
# 실행 명령어 예시
# VS Code의 PowerShell 터미널에서 입력합니다.
# ============================================================

# 전체 Excel 파일 업데이트
# py update_excel.py

# Nona 1만 업데이트
# py update_excel.py -o "Nona 1"

# Nona 1과 Octa 1만 업데이트
# py update_excel.py -o "Nona 1" "Octa 1"

# 세미콜론으로 여러 파일 업데이트
# PowerShell에서는 세미콜론 전체를 따옴표로 감싸야 합니다.
# py update_excel.py -o "Nona 1; Octa 1"

# 확장자를 붙여도 됩니다.
# py update_excel.py -o "Nona 1.xlsx" "Octa 1.xlsx"

# 사용할 수 있는 옵션 설명 보기
# py update_excel.py --help
