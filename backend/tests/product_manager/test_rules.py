"""ADR-051's pure rules: image sizes read from the bytes, the publish
checklist, the status the Product Manager shows, and the manifest rule that
lets a product without Workeasy360 sign-in register a launch URL."""

from __future__ import annotations

import pytest

from tests.product_manager.conftest import jpeg, png
from workeasy360.commercial.catalog import domain
from workeasy360.contracts.enums import ProductMediaKind, ProductStatus
from workeasy360.contracts.errors import CatalogValidationError
from workeasy360.contracts.values.manifest import (
    MANIFEST_VERSION,
    ManifestError,
    parse_manifest,
)


def test_dimensions_are_read_from_png_and_jpeg_bytes():
    assert domain.image_dimensions(png(300, 120)) == (300, 120)
    assert domain.image_dimensions(jpeg(1280, 720)) == (1280, 720)
    assert domain.image_dimensions(b"GIF89a....") is None
    assert domain.image_dimensions(b"\x89PNG\r\n\x1a\n") is None  # truncated
    assert domain.image_dimensions(b"\xff\xd8\xff\xe0\x00") is None  # truncated


def test_a_valid_logo_passes_and_returns_its_size():
    assert domain.validate_media(
        kind=ProductMediaKind.LOGO,
        content_type="image/png; charset=binary",
        data=png(256, 256),
    ) == ("image/png", 256, 256)


def test_a_banner_and_a_screenshot_have_their_own_bounds():
    with pytest.raises(CatalogValidationError):
        domain.validate_media(
            kind=ProductMediaKind.BANNER, content_type="image/png", data=png(256, 256)
        )
    assert domain.validate_media(
        kind=ProductMediaKind.BANNER, content_type="image/png", data=png(1200, 300)
    )[1:] == (1200, 300)


def test_the_checklist_names_each_missing_requirement_in_order():
    items = domain.publish_checklist(
        name="App",
        slug="app",
        category="",
        tagline="",
        has_logo=False,
        application_url="",
        launch_url="https://app.workeasy360.com/",
        status=ProductStatus.DEPRECATED,
    )
    assert [i["label"] for i in items if not i["ok"]] == [
        "Category",
        "Short description",
        "Logo",
        "Application URL",
        "Not archived",
    ]


@pytest.mark.parametrize(
    ("status", "shown"),
    [
        (ProductStatus.DRAFT, "draft"),
        (ProductStatus.AVAILABLE, "active"),
        (ProductStatus.DEPRECATED, "archived"),
        (ProductStatus.RETIRED, "archived"),
    ],
)
def test_the_three_states_the_product_manager_shows(status, shown):
    assert domain.MANAGER_STATUS[status] == shown


def test_archiving_a_draft_is_a_legal_reversible_move():
    domain.assert_product_transition(ProductStatus.DRAFT, ProductStatus.DEPRECATED)
    domain.assert_product_transition(ProductStatus.DEPRECATED, ProductStatus.AVAILABLE)


def test_a_slug_becomes_its_code():
    assert domain.code_for_slug("easy-help-desk") == "easy_help_desk"
    with pytest.raises(CatalogValidationError):
        domain.code_for_slug("9-lives")


def _manifest(**fields):
    return {
        "manifest_version": MANIFEST_VERSION,
        "product_code": "seedling",
        "launch_url": "https://seedling.workeasy360.com/",
        **fields,
    }


def test_a_local_only_product_needs_no_redirect_uri():
    parsed = parse_manifest(_manifest(sign_in_mode="local_only"))
    assert parsed.redirect_uris == ()


@pytest.mark.parametrize("mode", [None, "sso_only"])
def test_a_product_using_workeasy360_sign_in_still_needs_one(mode):
    fields = {"sign_in_mode": mode} if mode else {}
    with pytest.raises(ManifestError) as raised:
        parse_manifest(_manifest(**fields))
    assert raised.value.field == "redirect_uris"
