from http import HTTPStatus

from flask import render_template
from werkzeug.exceptions import HTTPException

from services.errors import ServiceError


def _title(code: int) -> str:
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return "Error"


def register_error_handlers(app) -> None:
    def render(code: int, message: str):
        return render_template("error.html", status=code, title=_title(code), message=message), code

    @app.errorhandler(ServiceError)
    def handle_service_error(err: ServiceError):
        return render(err.status_code, err.message)

    @app.errorhandler(HTTPException)
    def handle_http_error(err: HTTPException):
        # Unhandled exceptions arrive here as InternalServerError with a generic
        # description; stack traces are never shown to the browser.
        return render(err.code or 500, err.description or "Something went wrong.")
