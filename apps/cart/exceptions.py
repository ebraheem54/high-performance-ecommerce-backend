"""
Custom exceptions for cart app.
Allows views to return precise HTTP status codes per exception type.
"""


class ProductNotFoundError(Exception):
    """Raised when a product does not exist or is inactive. → HTTP 404"""
    pass


class OutOfStockError(Exception):
    """Raised when requested quantity exceeds available stock. → HTTP 404"""
    pass
