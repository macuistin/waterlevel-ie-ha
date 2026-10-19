"""Shared fixtures for Waterlevel.ie tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest


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
