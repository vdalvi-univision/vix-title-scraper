"""Public error types for the scraper."""


class ScraperError(RuntimeError):
    """Base error for scrape/export failures."""


class ConfigError(ScraperError):
    """Missing or invalid configuration."""


class GraphQLError(ScraperError):
    """HTTP or GraphQL response failure."""


class GraphQLComplexityError(GraphQLError):
    """Query exceeded the GraphQL complexity budget (retry with a smaller page)."""


class PaginationError(ScraperError):
    """Cursor cycle or unsupported multi-cursor pagination."""


class ExtractError(ScraperError):
    """Payload shape cannot be extracted safely."""
