"""Exception types and their HTTP status mapping.

Raised by the domain layers and turned into responses in routes.py.
"""


class CheckoutError(Exception):
    """Base class for every error the route layer turns into a response."""


class ValidationFailure(CheckoutError):
    """A request payload failed one or more checks."""

    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class CartNotFound(CheckoutError):
    def __init__(self, cart_id):
        super().__init__("cart %s not found" % cart_id)
        self.cart_id = cart_id


class OrderNotFound(CheckoutError):
    def __init__(self, order_id):
        super().__init__("order %s not found" % order_id)
        self.order_id = order_id


class PricingFailure(CheckoutError):
    """A line item has no active price row."""

    def __init__(self, product_id):
        super().__init__("no active price for product %s" % product_id)
        self.product_id = product_id


class ReservationFailure(CheckoutError):
    """The inventory service declined a stock hold."""

    def __init__(self, product_id, status_code):
        super().__init__(
            "reservation for product %s returned %d" % (product_id, status_code)
        )
        self.product_id = product_id
        self.status_code = status_code


class WebhookDeliveryFailure(CheckoutError):
    def __init__(self, url, status_code):
        super().__init__("delivery to %s returned %d" % (url, status_code))
        self.url = url
        self.status_code = status_code


# Route handlers map exception types to response codes through this table.
_STATUS_BY_TYPE = {
    ValidationFailure: 400,
    CartNotFound: 404,
    OrderNotFound: 404,
    PricingFailure: 409,
    ReservationFailure: 409,
    WebhookDeliveryFailure: 502,
}


def status_code_for(exc):
    """Returns the HTTP status for one raised CheckoutError."""
    return _STATUS_BY_TYPE.get(type(exc), 500)
