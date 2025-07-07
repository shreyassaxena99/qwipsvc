from unittest.mock import MagicMock, patch

from svc.email_manager import send_access_email


@patch("smtplib.SMTP_SSL")
def test_send_access_email_sends_successfully(mock_smtp_ssl, sample_booking):
    mock_smtp = MagicMock()
    mock_smtp_ssl.return_value.__enter__.return_value = mock_smtp

    send_access_email("user@example.com", sample_booking)

    mock_smtp.login.assert_called_once()
    sent_message = mock_smtp.send_message.call_args[0][0]

    assert "Your Qwip Booking at 8 Cutter Lane" in sent_message["Subject"]
    assert sent_message["To"] == "user@example.com"
    assert "**Access Code**: 561671" in sent_message.get_content()
