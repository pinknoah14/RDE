import io

import polars as pl


def _decode(content: bytes) -> str:
    for enc in ["cp949", "utf-8"]:
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 인코딩 읽기 실패")


def parse_pivot_csv(content: bytes, col_map: dict[str, str] | None = None) -> pl.DataFrame:
    """
    피벗 판매 CSV (wide format) → long format DataFrame.

    입력 헤더: 상품코드, 센터, [YYYY-MM-DD, ...]
    출력 컬럼: 상품코드, 센터, 판매일자, 판매수량
    """
    raw = _decode(content)
    wide = pl.read_csv(io.StringIO(raw), null_values=["", " "])

    sku_col    = col_map["col_pivot_sku"]    if col_map else "상품코드"
    center_col = col_map["col_pivot_center"] if col_map else "센터"

    date_cols = [c for c in wide.columns if c not in (sku_col, center_col)]
    if not date_cols:
        raise ValueError("피벗 CSV에 날짜 컬럼이 없습니다")

    long = wide.unpivot(
        on=date_cols,
        index=[sku_col, center_col],
        variable_name="판매일자",
        value_name="판매수량",
    )

    rename_map: dict[str, str] = {}
    if sku_col != "상품코드":
        rename_map[sku_col] = "상품코드"
    if center_col != "센터":
        rename_map[center_col] = "센터"
    if rename_map:
        long = long.rename(rename_map)

    long = long.with_columns(
        pl.col("판매수량").cast(pl.Int32, strict=False).fill_null(0)
    )
    return long.filter(pl.col("판매수량") >= 0)


def parse_outbound_csv(content: bytes, col_map: dict[str, str] | None = None) -> pl.DataFrame:
    """
    출고현황 CSV (long format).
    필수 컬럼: 상품코드, 센터, 판매일자, 판매수량
    """
    raw = _decode(content)

    sku_col    = col_map["col_out_sku"]    if col_map else "상품코드"
    center_col = col_map["col_out_center"] if col_map else "센터"
    date_col   = col_map["col_out_date"]   if col_map else "판매일자"
    qty_col    = col_map["col_out_qty"]    if col_map else "판매수량"

    df = pl.read_csv(
        io.StringIO(raw),
        columns=[sku_col, center_col, date_col, qty_col],
        schema_overrides={
            sku_col:    pl.Utf8,
            center_col: pl.Utf8,
            date_col:   pl.Utf8,
            qty_col:    pl.Int32,
        },
        null_values=["", " "],
    )

    rename_map: dict[str, str] = {}
    if sku_col    != "상품코드": rename_map[sku_col]    = "상품코드"
    if center_col != "센터":    rename_map[center_col] = "센터"
    if date_col   != "판매일자": rename_map[date_col]   = "판매일자"
    if qty_col    != "판매수량": rename_map[qty_col]    = "판매수량"
    if rename_map:
        df = df.rename(rename_map)

    return df.filter(pl.col("판매수량") >= 0)
