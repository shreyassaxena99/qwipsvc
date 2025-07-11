import logging

import resend

from svc.env import resend_api_key
from svc.models import BookingDetails
from svc.utils import format_datetime_for_email

HELLO_EMAIL = "shreyas@qwip.co.uk"

resend.api_key = resend_api_key

logger = logging.getLogger(__name__)


def _create_email_message(booking: BookingDetails) -> dict[str, str]:
    formatted_start_time = format_datetime_for_email(booking.start_time.isoformat())
    subject = f"Your Qwip Booking at {booking.address} from {formatted_start_time}"
    content = f"""
<p>Hi there!</p><br>
                        
<p>Thanks for booking with Qwip! Your booking details are shown below</p><br>
    
<strong>Start Time: {booking.start_time}</strong><br>
<strong>Access Code: {booking.access_code}</strong><br>

<p>To access your workspace, please go to {booking.address}, and enter your access
code on the booth.<p><br>

<strong>Please make sure you press the Yale Button to enter your access code once typed</strong>.<br>

<strong>Once done with using the workspace, please click the button below to end your booking:<strong><br>

<form action="https://qwip.co.uk/end-session/{booking.booking_id}">
    <input type="submit" value="End Booking Now" />
</form>

<p>Thank You for using Qwip!</p><br>
    """.strip()

    return {"subject": subject, "content": content}


def send_access_email(customer_email: str, booking: BookingDetails):
    message_metadata = _create_email_message(booking)

    r: resend.Email = resend.Emails.send(
        {
            "from": HELLO_EMAIL,
            "to": customer_email,
            "subject": message_metadata["subject"],
            "html": message_metadata["content"],
        }
    )

    if r["id"]:
        logger.info(f"Email sent successfully to {customer_email} with ID: {r["id"]}")
