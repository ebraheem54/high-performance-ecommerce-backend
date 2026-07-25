"""
Middleware for recording Prometheus app request metrics.
"""

from apps.core.metrics import now, observe_request


class PrometheusMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started_at = now()
        try:
            response = self.get_response(request)
        except Exception:
            observe_request(request, 500, started_at)
            raise

        observe_request(request, response.status_code, started_at)
        return response
