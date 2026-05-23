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


def parse_pivot_csv(content: bytes, center_cd: str = "GGH1") -> pl.DataFrame:
    """
    피벗 판매 CSV (wide format) → long format DataFrame.

    입력 헤더: 상품코드, [센터,] [YYYY-MM-DD, ...]
    출력 컬럼: 상품코드, 센터, 판매일자, 판매수량
    """
    raw = _decode(content)
    wide = pl.read_csv(io.StringIO(raw), null_values=["", " "])

    sku_col = next((c for c in ["상품코드", "상품번호", "SKU", "sku"] if c in wide.columns), None)
    if sku_col is None:
        raise ValueError(f"상품코드 컬럼을 찾을 수 없습니다. 유효한 컬럼: {wide.columns}")

    has_center = "센터" in wide.columns
    index_cols = [sku_col] + (["센터"] if has_center else [])
    date_cols = [c for c in wide.columns if c not in index_cols]
    if not date_cols:
        raise ValueError("피벗 CSV에 날짜 컬럼이 없습니다")

    long = wide.unpivot(
        on=date_cols,
        index=index_cols,
        variable_name="판매일자",
        value_name="판매수량",
    )
    long = long.rename({sku_col: "상품코드"})
    if not has_center:
        long = long.with_columns(pl.lit(center_cd).alias("센터"))

    long = long.with_columns(
        pl.col("판매수량").cast(pl.Int32, strict=False).fill_null(0)
    )
    return long.filter(pl.col("판매수량") >= 0).select(
        ["상품코드", "센터", "판매일자", "판매수량"]
    )


def parse_outbound_csv(content: bytes, center_cd: str = "GGH1") -> pl.DataFrame:
    """
    출고현황 CSV → 판매 long format DataFrame.

    필수: 상품코드
    날짜: 판매일자 > 문서일자 > 배송요청일 순으로 자동 탐색
    수량: 판매수량 > 주문수량 > 원주문수량 순으로 자동 탐색
    센터: 컬럼 없으면 center_cd 파라미터 사용
    """
    raw = _decode(content)
    df = pl.read_csv(io.StringIO(raw), null_values=["", " "], infer_schema_length=1000)

    if "상품코드" not in df.columns:
        raise ValueError(f"'상품코드' 컬럼이 없습니다. 유효한 컬럼: {df.columns}")

    date_col = next(
        (c for c in ["판매일자", "문서일자", "배송요청일"] if c in df.columns), None
    )
    if date_col is None:
        raise ValueError(f"날짜 컬럼(판매일자/문서일자/배송요청일)을 찾을 수 없습니다. 유효한 컬럼: {df.columns}")

    qty_col = next(
        (c for c in ["판매수량", "주문수량", "원주문수량"] if c in df.columns), None
    )
    if qty_col is None:
        raise ValueError(f"수량 컬럼(판매수량/주문수량/원주문수량)을 찾을 수 없습니다. 유효한 컬럼: {df.columns}")

    센터_expr = pl.col("센터").cast(pl.Utf8) if "센터" in df.columns else pl.lit(center_cd)

    result = df.select([
        pl.col("상품코드").cast(pl.Utf8).alias("상품코드"),
        센터_expr.alias("센터"),
        pl.col(date_col).cast(pl.Utf8).alias("판매일자"),
        pl.col(qty_col).cast(pl.Int32, strict=False).fill_null(0).alias("판매수량"),
    ])
    return result.filter(pl.col("판매수량") >= 0)

