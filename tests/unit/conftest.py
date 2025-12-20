from datetime import datetime
from unittest.mock import patch

import pytest

from svc.models import SessionDetails


@pytest.fixture
def sample_booking():
    return SessionDetails(
        pod_name="Test Pod",
        address="8 Cutter Lane, London, UK",
        start_time=datetime(2025, 7, 7, 14, 0),
        access_code="561671",
        session_token="e6cf97",
    )


@pytest.fixture
def mock_stripe_client():
    with patch("svc.payments_manager.create_stripe_client") as mock:
        yield mock
