"""Shared fixtures for Waterlevel.ie tests."""

from collections.abc import Generator
import inspect
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

# Hydro-Data files read once per station, in the background. A test that doesn't
# mock them gets a 404, as for a station with no such file.
_OPTIONAL_HYDRO_FILES = ("/index.json", "/alarmlevel.json")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
def frozen_now(freezer):
    """Run every test at a fixed time just after the readings in the test data.

    Readings older than the stale limit make a station unavailable, so tests must
    not depend on the real clock.
    """
    freezer.move_to("2026-10-19T18:30:00Z")
    return freezer


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Stop a finished config flow from setting the entry up for real."""
    with patch(
        "custom_components.waterlevel_ie.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Create entities that are disabled by default as enabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture(autouse=True)
def optional_hydro_files_are_missing() -> Generator[None]:
    """Answer 404 for a station's metadata and alarm-level files unless a test mocks them."""
    original = AiohttpClientMocker.match_request

    async def match_request(self, method, url, *args, **kwargs):
        try:
            result = original(self, method, url, *args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        except AssertionError:
            if "/hydro-data/" in str(url) and str(url).endswith(_OPTIONAL_HYDRO_FILES):
                return AiohttpClientMockResponse(method, url, status=404)
            raise

    with patch.object(AiohttpClientMocker, "match_request", match_request):
        yield
