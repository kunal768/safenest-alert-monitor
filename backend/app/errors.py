from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, error: str, message: str, status_code: int = 400):
        self.error = error
        self.message = message
        self.status_code = status_code


def error_body(error: str, message: str) -> dict:
    return {"error": error, "message": message}


async def app_error_handler(_request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.error, exc.message))


async def http_error_handler(_request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body("http_error", str(exc.detail)),
    )


async def validation_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=error_body("validation_error", str(exc.errors())),
    )
