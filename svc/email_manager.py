import logging
import smtplib
from email.message import EmailMessage

from svc.env import email_password
from svc.models import BookingDetails

HELLO_EMAIL = "shreyas@qwip.co.uk"
APP_PASSWORD = email_password

logger = logging.getLogger(__name__)


def _create_email_message(customer_email: str, booking: BookingDetails) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = (
        f"Your Qwip Booking at {booking.address} from {booking.start_time}"
    )
    message["From"] = HELLO_EMAIL
    message["To"] = customer_email
    message.set_content(
        f"""
Hi there!
                        
Thanks for booking with Qwip! Your booking details are shown below
    
**Start Time**: {booking.start_time}
**Access Code**: {booking.access_code}

To access your workspace, please go to {booking.address}, and enter your access
code on the booth. 

**Please make sure you press the Yale Button to enter your access code once typed**.

Once done with using the workspace, please click the link below to end your booking:

https://qwip.co.uk/end_booking/{booking.booking_id}

Thank You for using Qwip!
    """.strip()
    )

    return message


def send_access_email(customer_email: str, booking: BookingDetails):
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(HELLO_EMAIL, APP_PASSWORD)
            smtp.send_message(_create_email_message(customer_email, booking))
        logger.info(f"Email sent to {customer_email}")
    except Exception as e:
        logger.info(f"Failed to send email: {str(e)}")
