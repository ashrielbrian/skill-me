from ..http import Request, Response, json_response, route
from ..services import quotes as quotes_service
from ..validation import parse_country, parse_positive_int, require_fields


@route("POST", "/quotes")
def create_quote(request: Request) -> Response:
    require_fields(request.body, "carrier", "weight_g", "dest_country")
    result = quotes_service.quote(
        request.body["carrier"],
        parse_positive_int(request.body["weight_g"], "weight_g"),
        parse_country(request.body["dest_country"]),
    )
    return json_response(result)
