"""ViX uiPage GraphQL scraper and website explorer — stdlib-only."""

from vix_scraper.explorer import ExploreResult, SiteExplorer, run_explore
from vix_scraper.exporter import CsvExporter
from vix_scraper.extractor import TitleExtractor
from vix_scraper.layout_compare import BatchResult, run_batch_scrape
from vix_scraper.models import ExportedImage, ExportedPage, ExportedTitle, ScrapeConfig
from vix_scraper.scraper import TitleScraper

__all__ = [
    "BatchResult",
    "CsvExporter",
    "ExploreResult",
    "ExportedImage",
    "ExportedPage",
    "ExportedTitle",
    "ScrapeConfig",
    "SiteExplorer",
    "TitleExtractor",
    "TitleScraper",
    "run_batch_scrape",
    "run_explore",
]

__version__ = "1.2.0"
