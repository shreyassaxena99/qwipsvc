from unittest.mock import ANY, MagicMock, patch

from svc.payments_manager import (charge_user, create_stripe_event,
                                  get_user_data, process_event)


@patch("svc.payments_manager._create_stripe_client")
def test_get_user_data_creates_setup_intent(mock_create_client):
    mock_customer = MagicMock(id="cus_test")
    mock_setup_intent = MagicMock()
    mock_client = MagicMock()
    mock_client.customers.create.return_value = mock_customer
    mock_client.setup_intents.create.return_value = mock_setup_intent
    mock_create_client.return_value = mock_client

    result = get_user_data("Pod-1")
    assert result == mock_setup_intent
    mock_client.setup_intents.create.assert_called_once_with(
        customer="cus_test", usage="off_session", metadata={"pod_name": "Pod-1"}
    )


@patch("svc.payments_manager.Webhook.construct_event")
def test_create_stripe_event_constructs_event(mock_construct_event):
    mock_event = MagicMock()
    mock_construct_event.return_value = mock_event

    payload = {"type": "setup_intent.succeeded"}
    header = "test_header"

    event = create_stripe_event(payload, header)
    assert event == mock_event
    mock_construct_event.assert_called_once_with(payload, header, ANY)


@patch("svc.payments_manager._process_setup_intent_success")
@patch("svc.payments_manager._create_stripe_client")
def test_process_event_calls_success_handler(mock_create_client, mock_process_success):
    mock_event = MagicMock(type="setup_intent.succeeded", data=MagicMock(object={}))
    mock_client = MagicMock()
    mock_create_client.return_value = mock_client

    process_event(mock_event)
    mock_process_success.assert_called_once_with(mock_client, mock_event)


@patch("svc.payments_manager._create_stripe_client")
def test_charge_user_creates_payment_intent(mock_create_client):
    mock_client = MagicMock()
    mock_create_client.return_value = mock_client

    session_data = {
        "stripe_customer_id": "cus_test",
        "stripe_payment_method": "pm_test",
    }

    charge_user(session_data, 420)

    mock_client.payment_intents.create.assert_called_once_with(
        customer="cus_test",
        payment_method="pm_test",
        amount=420,
        currency="gbp",
        confirm=True,
        off_session=True,
    )
