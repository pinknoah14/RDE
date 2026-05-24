"""RDE 통일 에러 응답.

모든 에러는 {code, message, detail} 형식으로 응답.
"""
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging_config import get_logger


logger = get_logger("exception")


class RDEException(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        detail: str = "",
        status_code: int = 400,
    ):
        self.code = code
        self.message = message
        self.detail = detail
        self.status_code = status_code
        super().__init__(message)


def rde_error(code: str, message: str, detail: str = "", status_code: int = 400) -> JSONResponse:
    """편의 함수: JSONResponse 직접 반환."""
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "detail": detail},
    )


async def rde_exception_handler(request: Request, exc: RDEException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "입력값 오류",
            "detail": str(exc.errors()),
        },
    )


async def http_exception_handler(request: Request, exc) -> JSONResponse:
    """FastAPI HTTPException → 통일 형식.

    기존 코드가 raise HTTPException(...)을 그대로 쓰는 동안 점진 마이그레이션 지원.
    detail이 dict 형태로 code/message를 담고 있으면 그대로 통과시킨다.
    """
    status_code = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", "")
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        return JSONResponse(status_code=status_code, content=detail)

    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
    }
    code = code_map.get(status_code, "HTTP_ERROR")
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": str(detail) if detail else "요청을 처리할 수 없습니다.",
            "detail": str(detail) if detail else "",
        },
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("처리되지 않은 예외", path=str(request.url.path), error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "서버 오류가 발생했습니다.",
            "detail": str(exc),
        },
    )
