"""Layout diff reports row slot (carousel_x), not page-wide position as Position."""

from __future__ import annotations

from pathlib import Path

from vix_scraper.layout_compare import write_layout_diff_summary
from vix_scraper.models import ExportedTitle


def _t(
    *,
    title: str,
    content_id: str,
    position: int,
    x: int,
    y: int,
    row_title: str,
    is_hero: str = "false",
    video_type: str = "MOVIE",
    module_type: str = "VIDEO_CAROUSEL",
) -> ExportedTitle:
    return ExportedTitle(
        position=position,
        title=title,
        row_title=row_title,
        carousel_x=x,
        carousel_y=y,
        description="",
        date_released="",
        genres="",
        content_id=content_id,
        video_type=video_type,
        module_type=module_type,
        is_hero=is_hero,
    )


def test_layout_diff_shifts_use_slot_not_page_index(tmp_path: Path):
    baseline = [
        _t(title="Same slot", content_id="id:a", position=14, x=1, y=4, row_title="Lo más buscado"),
        _t(title="Moved slot", content_id="id:b", position=20, x=1, y=5, row_title="Novedades"),
    ]
    variant = [
        _t(title="Same slot", content_id="id:a", position=8, x=1, y=3, row_title="Lo más buscado"),
        _t(title="Moved slot", content_id="id:b", position=21, x=3, y=5, row_title="Novedades"),
    ]
    out = tmp_path / "layout_diff_summary.md"
    write_layout_diff_summary(
        baseline,
        variant,
        baseline_path="odp.csv",
        variant_path="wc.csv",
        output=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "page_index" in text
    assert "Largest placement shifts" in text
    assert "pos=" not in text
    # Same row name + slot: page-index-only change is not a placement shift
    assert "Same slot" not in text.split("## Largest placement shifts")[1].split("##")[0]
    assert "Moved slot" in text
    assert "slot 1→3" in text

    shift_csv = tmp_path / "layout_diff_summary_shifts.csv"
    assert shift_csv.is_file()
    body = shift_csv.read_text(encoding="utf-8")
    assert "base_page_index" in body
    assert "base_pos" not in body
    assert "id:b" in body
    assert "id:a" not in body


def test_layout_diff_sample_ids_label_page_index(tmp_path: Path):
    baseline = [_t(title="Only base", content_id="id:only", position=552, x=2, y=24, row_title="Explorar")]
    variant = [_t(title="Only var", content_id="id:var", position=10, x=1, y=1, row_title="Hero", is_hero="true")]
    out = tmp_path / "layout_diff_summary.md"
    write_layout_diff_summary(
        baseline,
        variant,
        baseline_path="a.csv",
        variant_path="b.csv",
        output=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "page_index=552" in text
    assert "x=2" in text
    assert "@ pos=" not in text


def test_layout_diff_keeps_empty_cms_rows_with_zero_titles(tmp_path: Path):
    baseline = [
        _t(
            title="(empty)",
            content_id="empty:cw",
            position=4,
            x=1,
            y=4,
            row_title="Seguir viendo",
            video_type="EMPTY",
            module_type="CONTINUE_WATCHING_CAROUSEL",
        ),
        _t(
            title="(empty)",
            content_id="empty:byou",
            position=5,
            x=1,
            y=5,
            row_title="Porque viste La Rosa",
            video_type="EMPTY",
            module_type="BECAUSE_YOU_CAROUSEL",
        ),
        _t(
            title="Shown",
            content_id="id:shown",
            position=6,
            x=1,
            y=6,
            row_title="Lo más buscado",
        ),
    ]
    out = tmp_path / "layout_diff_summary.md"
    write_layout_diff_summary(
        baseline,
        baseline,
        baseline_path="odp.csv",
        variant_path="odp.csv",
        output=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "- 4: 'Seguir viendo' | CONTINUE_WATCHING_CAROUSEL | n=0" in text
    assert "- 5: 'Porque viste La Rosa' | BECAUSE_YOU_CAROUSEL | n=0" in text
    assert "- 6: 'Lo más buscado' | VIDEO_CAROUSEL | n=1" in text


def test_batch_scrape_warns_jwt_country_mismatch(tmp_path: Path, monkeypatch):
    import base64
    import json
    import time

    from vix_scraper.layout_compare import load_scrape_meta, run_batch_scrape
    from vix_scraper.models import ScrapeConfig

    monkeypatch.delenv("AUTH_TOKEN_AUTH0", raising=False)
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("VIX_COUNTRY", raising=False)

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + 60, "country": "US"}).encode("ascii")
    ).decode().rstrip("=")
    token = f"{header}.{payload}.sig"
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        url_paths=["/ondemandplus"],
        endpoint="https://example.invalid/gql",
        query="query { __typename }",
        auth_token=token,
        country="MX",
        output_dir=tmp_path,
    )
    monkeypatch.setattr(
        "vix_scraper.layout_compare.resolve_working_endpoint",
        lambda config, probe_path="/ondemandplus": (
            config.endpoint or "https://example.invalid/gql",
            "Using test endpoint",
        ),
    )
    monkeypatch.setattr(
        "vix_scraper.layout_compare.probe_page_exists",
        lambda config, url_path: (True, "ok"),
    )
    monkeypatch.setattr(
        "vix_scraper.layout_compare.TitleScraper.scrape",
        lambda self: [],
    )
    result = run_batch_scrape(cfg)
    assert all(o.status == "ok" for o in result.outcomes)
    assert any("JWT country=US" in n and "catalog country=MX" in n for n in result.notes)
    meta = load_scrape_meta(tmp_path)
    assert meta is not None
    assert meta["country"] == "MX"
    assert meta["jwt_country"] == "US"
    assert meta["status"] == "ok"
    assert any("JWT country=US" in n for n in (meta.get("notes") or []))
    dumped = json.dumps(meta)
    assert token not in dumped


def test_one_page_graphql_error_does_not_abort_the_other(tmp_path, monkeypatch):
    import base64
    import json
    import time

    from vix_scraper.errors import GraphQLError
    from vix_scraper.layout_compare import load_scrape_meta, run_batch_scrape
    from vix_scraper.models import ExportedTitle, ScrapeConfig

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + 60, "country": "MX"}).encode("ascii")
    ).decode().rstrip("=")
    token = f"{header}.{payload}.sig"
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        url_paths=["/ondemandplus", "/ondemandpluswc"],
        endpoint="https://example.invalid/gql",
        query="query { __typename }",
        auth_token=token,
        country="MX",
        output_dir=tmp_path,
    )
    monkeypatch.setattr(
        "vix_scraper.layout_compare.resolve_working_endpoint",
        lambda config, probe_path="/ondemandplus": (
            config.endpoint or "https://example.invalid/gql",
            "Using test endpoint",
        ),
    )
    monkeypatch.setattr(
        "vix_scraper.layout_compare.probe_page_exists",
        lambda config, url_path: (True, "ok"),
    )

    def scrape(self):
        if self.config.url_path == "/ondemandplus":
            raise GraphQLError(
                'Cannot query field "displayLimit" on type "UiVideoCarousel".'
            )
        return [
            ExportedTitle(
                position=1,
                title="A",
                row_title="Hero",
                carousel_x=1,
                carousel_y=1,
                description="",
                date_released="",
                genres="",
                content_id="a",
                video_type="MOVIE",
                page_url_path="/ondemandpluswc",
            )
        ]

    monkeypatch.setattr("vix_scraper.layout_compare.TitleScraper.scrape", scrape)
    result = run_batch_scrape(cfg)
    statuses = {o.url_path: o.status for o in result.outcomes}
    assert statuses["/ondemandplus"] == "error"
    assert statuses["/ondemandpluswc"] == "ok"
    meta = load_scrape_meta(tmp_path)
    assert meta is not None
    assert meta["status"] == "partial"
    assert meta["row_counts"]["/ondemandpluswc"] == 1
    assert token not in json.dumps(meta)


def test_page_csv_name_for_custom_paths():
    from vix_scraper.layout_compare import page_csv_name, path_slug

    assert path_slug("/movies") == "movies"
    assert page_csv_name("/deportes") == "deportes_titles.csv"
    assert page_csv_name("/foo/bar") == "foo_bar_titles.csv"


def test_run_batch_scrape_custom_third_path(tmp_path, monkeypatch):
    from vix_scraper.layout_compare import load_scrape_meta, page_csv_name, run_batch_scrape
    from vix_scraper.models import ExportedTitle, ScrapeConfig

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        url_paths=["/ondemandplus", "/ondemandpluswc", "/movies"],
        endpoint="https://example.invalid/gql",
        query="query { __typename }",
        auth_token="dummy-token",
        country="MX",
        output_dir=tmp_path,
    )
    captured: list[str] = []
    monkeypatch.setattr(
        "vix_scraper.layout_compare.resolve_working_endpoint",
        lambda config, probe_path="/ondemandplus": (
            config.endpoint or "https://example.invalid/gql",
            "Using test endpoint",
        ),
    )
    monkeypatch.setattr(
        "vix_scraper.layout_compare.probe_page_exists",
        lambda config, url_path: (True, "ok"),
    )

    def scrape(self):
        captured.append(self.config.url_path)
        return [
            ExportedTitle(
                position=1,
                title="T",
                row_title="Hero",
                carousel_x=1,
                carousel_y=1,
                description="",
                date_released="",
                genres="",
                content_id=f"id:{self.config.url_path}",
                video_type="MOVIE",
                page_url_path=self.config.url_path,
            )
        ]

    monkeypatch.setattr("vix_scraper.layout_compare.TitleScraper.scrape", scrape)
    result = run_batch_scrape(cfg)
    assert captured == ["/ondemandplus", "/ondemandpluswc", "/movies"]
    assert {o.url_path for o in result.outcomes} == {
        "/ondemandplus",
        "/ondemandpluswc",
        "/movies",
    }
    assert (tmp_path / page_csv_name("/movies")).is_file()
    meta = load_scrape_meta(tmp_path)
    assert meta is not None
    assert meta["page_order"] == ["/ondemandplus", "/ondemandpluswc", "/movies"]
    assert meta["row_counts"]["/movies"] == 1


def test_run_batch_scrape_forces_lean_layout_query(tmp_path, monkeypatch):
    """Fat request.graphql / VIX_GRAPHQL_QUERY must not win for layout compare."""
    from vix_scraper.layout_compare import run_batch_scrape
    from vix_scraper.models import ScrapeConfig

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        url_paths=["/ondemandplus"],
        endpoint="https://example.invalid/gql",
        query="query Fat { video { contributors imageAssets } }",
        query_file=Path("queries/request.graphql"),
        auth_token="dummy-token",
        country="MX",
        output_dir=tmp_path,
    )
    captured: dict = {}
    monkeypatch.setattr(
        "vix_scraper.layout_compare.resolve_working_endpoint",
        lambda config, probe_path="/ondemandplus": (
            config.endpoint or "https://example.invalid/gql",
            "Using test endpoint",
        ),
    )
    monkeypatch.setattr(
        "vix_scraper.layout_compare.probe_page_exists",
        lambda config, url_path: (True, "ok"),
    )

    def scrape(self):
        captured["query"] = self.config.query
        captured["query_file"] = self.config.query_file
        captured["contents_first_from_total"] = self.config.contents_first_from_total
        captured["paginate_contents"] = self.config.paginate_contents
        return []

    monkeypatch.setattr("vix_scraper.layout_compare.TitleScraper.scrape", scrape)
    run_batch_scrape(cfg)
    assert captured["query"] is None
    assert captured["query_file"] is not None
    assert Path(captured["query_file"]).name == "layout.graphql"
    assert captured["contents_first_from_total"] is True
    assert captured["paginate_contents"] is True

