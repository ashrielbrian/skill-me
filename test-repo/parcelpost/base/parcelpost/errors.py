class AppError(Exception):
    status = 500
    code = "internal"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class ValidationError(AppError):
    status = 422
    code = "invalid_request"


class NotFound(AppError):
    status = 404
    code = "not_found"


class Conflict(AppError):
    status = 409
    code = "conflict"


class UpstreamError(AppError):
    status = 502
    code = "upstream_unavailable"
