import io
from typing import Optional

import polars as pl


def _decode(content: bytes) -> str:
    for enc in ["cp949", "utf-8"]:
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 인코딩 읽기 실패")


def parse_pivot_csv(content: bytes) -> pl.DataFrame:
    """
    피벗 판매 CSV (wide format) → long format DataFrame.

    입력 헤더: 상품코드, 센터, [YYYY-MM-DD, ...]
    출력 컬럼: 상품코드, 센터, 판매일자, 판매수량
    """
    raw = _decode(content)
    wide = pl.read_csv(io.StringIO(raw), null_values=["", " "])

    date_cols = [c for c in wide.columns if c not in ("상품코드", "센터")]
    if not date_cols:
        raise ValueError("피벗 CSV에 날짜 컬럼이 없습니다")

    long = wide.unpivot(
        on=date_cols,
        index=["상품코드", "센터"],
        variable_name="판매일자",
        value_name="판매수량",
    )
    long = long.with_columns(
        pl.col("판매수량").cast(pl.Int32, strict=False).fill_null(0)
    )
    return long.filter(pl.col("판매수량") >= 0)


def parse_outbound_csv(content: bytes) -> pl.DataFrame:
    """
    출고현황 CSV (long format).
    필수 컬럼: 상품코드, 센터, 판매일자, 판매수량
    """
    raw = _decode(content)
    df = pl.read_csv(
        io.StringIO(raw),
        columns=["상품코드", "센터", "판매일자", "판매수량"],
        schema_overrides={
            "상품코드": pl.Utf8,
            "센터": pl.Utf8,
            "판매일자": pl.Utf8,
            "판매수량": pl.Int32,
        },
        null_values=["", " "],
    )
    return df.filter(pl.col("판매수량") >= 0)
