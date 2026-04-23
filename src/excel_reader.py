from __future__ import annotations

import io
from dataclasses import dataclass

import pandas as pd


@dataclass
class SheetData:
    name: str
    df: pd.DataFrame
    column_types: dict[str, str]
    summary_stats: str
    sample_markdown: str
    row_count: int
    col_count: int
    is_pivot: bool = False


@dataclass
class ExcelProfile:
    sheets: list[SheetData]
    sheet_count: int


def read_excel_profile(xlsx_bytes: bytes) -> ExcelProfile:
    stream = io.BytesIO(xlsx_bytes)
    try:
        xf = pd.ExcelFile(stream, engine="openpyxl")
    except Exception as e:
        raise ValueError(f"엑셀 파일을 열 수 없습니다: {e}")

    sheets = []
    for sheet_name in xf.sheet_names:
        try:
            df = pd.read_excel(xf, sheet_name=sheet_name, engine="openpyxl")
        except Exception:
            continue

        if df.empty:
            continue

        df = _handle_merged_cells(df)
        df = df.dropna(how="all").reset_index(drop=True)

        if df.empty:
            continue

        column_types = _infer_column_types(df)
        is_pivot = _detect_pivot_structure(df)

        try:
            summary_stats = df.describe(include="all").to_markdown()
        except Exception:
            summary_stats = "(통계 정보 없음)"

        sample_markdown = _sheet_to_markdown_sample(df, max_rows=50)

        sheets.append(SheetData(
            name=sheet_name,
            df=df,
            column_types=column_types,
            summary_stats=summary_stats,
            sample_markdown=sample_markdown,
            row_count=len(df),
            col_count=len(df.columns),
            is_pivot=is_pivot,
        ))

    if not sheets:
        raise ValueError("엑셀 파일에 분석 가능한 데이터 시트가 없습니다.")

    return ExcelProfile(sheets=sheets, sheet_count=len(sheets))


def _handle_merged_cells(df: pd.DataFrame) -> pd.DataFrame:
    return df.ffill(axis=0).ffill(axis=1)


def _infer_column_types(df: pd.DataFrame) -> dict[str, str]:
    types = {}
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            types[str(col)] = "date"
        elif pd.api.types.is_numeric_dtype(df[col]):
            types[str(col)] = "numeric"
        else:
            unique_ratio = df[col].nunique() / max(len(df), 1)
            types[str(col)] = "categorical" if unique_ratio < 0.5 else "text"
    return types


def _detect_pivot_structure(df: pd.DataFrame) -> bool:
    numeric_cols = sum(1 for c in df.columns if pd.api.types.is_numeric_dtype(df[c]))
    return numeric_cols / max(len(df.columns), 1) > 0.5 and len(df) > 3


def _sheet_to_markdown_sample(df: pd.DataFrame, max_rows: int = 50) -> str:
    sample = df.head(max_rows)
    try:
        return sample.to_markdown(index=False)
    except Exception:
        return sample.to_string(index=False)
