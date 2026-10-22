"""Failure taxonomy of the scraper."""


class UpstreamError(Exception):
    """Transport failure, 5xx or an unexpected upstream status — retryable."""


class Blocked(UpstreamError):
    """Silent empty body, 403 / 429 or a login wall — retryable on a fresh session."""


class BadRequest(Exception):
    """TikTok rejected the parameters — never retried."""
