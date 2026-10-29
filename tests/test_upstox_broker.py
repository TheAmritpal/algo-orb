from __future__ import annotations

import pytest
from upstox_client.rest import ApiException

from app.brokers.upstox import map_api_exception
from app.core.errors import BrokerRejected, BrokerUnavailable, BrokerUnknown


def test_maps_client_error_to_rejected():
    exc = ApiException(status=400, reason="bad request")
    with pytest.raises(BrokerRejected):
        map_api_exception(exc, submitting=True)


def test_maps_server_error_during_submission_to_unknown():
    exc = ApiException(status=500, reason="server error")
    with pytest.raises(BrokerUnknown):
        map_api_exception(exc, submitting=True)


def test_maps_transport_error_during_read_to_unavailable():
    with pytest.raises(BrokerUnavailable):
        map_api_exception(OSError("connection reset"), submitting=False)
