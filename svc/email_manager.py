import logging

import resend

from svc.custom_types import DictWithStringKeys
from svc.env import resend_api_key
from svc.models import SessionDetails
from svc.utils import format_datetime_for_email

HELLO_EMAIL = "hello@qwip.co.uk"
FOUNDER_EMAILS = ["shreyas@qwip.co.uk", "zain@qwip.co.uk", "stiofan@qwip.co.uk"]

resend.api_key = resend_api_key

logger = logging.getLogger(__name__)


# def _create_logo_attachment() -> resend.Attachment:
#     f: bytes = open("svc/assets/logo.jpg", "rb").read()
#     attachment: resend.Attachment = {
#         "content": list(f),
#         "filename": "logo.jpg",
#         "content_id": "logo-image",
#     }
#     return attachment


def _create_booking_email_message(session: SessionDetails) -> dict[str, str]:
    formatted_start_time = format_datetime_for_email(session.start_time.isoformat())
    subject = f"Your Qwip Session at {session.pod_name} from {formatted_start_time}"

    content = f"""
<html>
  <body style="font-family: Arial, sans-serif; background-color: #FAFAF8; padding: 20px; margin: 0;">
    <div style="max-width: 600px; margin: auto; background-color: #ffffff; padding: 0; border-radius: 8px; overflow: hidden;">
      
      <!-- Banner -->

      <!-- Content -->
      <div style="padding: 30px;">
        <h2 style="color: #1f3d32; margin-top: 0;">Thanks for booking with qwip!</h2>
        <p>Your session details are shown below:</p>

        <p><strong>Start Time:</strong> {formatted_start_time}</p>
        <p><strong>Access Code:</strong> {session.access_code}</p>

        <p>To access your workspace, please go to <strong>{session.address}</strong> and enter your access code on the pod's keypad.</p>

        <p>To enter the code, please enter the 5 digit code provided on the handle of the door.</p>

        <p>Click the button below to see how much your session is costing and end your session:</p>

        <a href="https://qwip.co.uk/session?t={session.session_token}"
           style="display:inline-block; padding:12px 20px; margin-top:15px; background-color:#1f3d32; color:#ffffff; text-decoration:none; border-radius:6px; font-weight:bold;">
          Manage Your Session
        </a>

        <p style="margin-top: 30px;">Thank you for using qwip!</p>

        <hr style="border: none; border-top: 1px solid #eee; margin: 40px 0;" />

        <p style="font-size: 12px; color: #777; margin: 0;">
          © 2025 Qwip Ltd. All rights reserved.<br />
          <a href="https://qwip.co.uk/privacy" style="color: #777; text-decoration: underline;">Privacy Policy</a> |
          <a href="https://qwip.co.uk/contact" style="color: #777; text-decoration: underline;">Contact Us</a><br />
          Qwip Ltd, 128 City Road, London, EC1V 2NX
        </p>
      </div>
    </div>
  </body>
</html>
"""

    return {"subject": subject, "content": content}


def _create_invalid_payment_email_message(
    session_details: DictWithStringKeys, cost_in_pence: int
) -> DictWithStringKeys:
    subject = "Issue with a Qwip session payment"

    content = f"""
<html>
  <body style="font-family: Arial, sans-serif; background-color: #FAFAF8; padding: 20px; margin: 0;">
    <div style="max-width: 600px; margin: auto; background-color: #ffffff; padding: 0; border-radius: 8px; overflow: hidden;">
      
      <!-- Banner -->

      <!-- Content -->
      <div style="padding: 30px;">
        
        There was an issue processing payment for the following session:<br/><br/>

        <strong>Session ID:</strong> {session_details["id"]}<br/>
        <strong>Customer Email:</strong> {session_details["user_email"]}<br/>
        <strong>Cost (in pence):</strong> {cost_in_pence}<br/><br/>
      
        <hr style="border: none; border-top: 1px solid #eee; margin: 40px 0;" />

        <p style="font-size: 12px; color: #777; margin: 0;">
          © 2025 Qwip Ltd. All rights reserved.<br />
          <a href="https://qwip.co.uk/privacy" style="color: #777; text-decoration: underline;">Privacy Policy</a> |
          <a href="https://qwip.co.uk/contact" style="color: #777; text-decoration: underline;">Contact Us</a><br />
          Qwip Ltd, 128 City Road, London, EC1V 2NX
        </p>
      </div>
    </div>
  </body>
</html>
"""
    return {"subject": subject, "content": content}


def send_access_email(customer_email: str, booking: SessionDetails):
    message_metadata = _create_booking_email_message(booking)

    r: resend.Emails.SendResponse = resend.Emails.send(
        {
            "from": HELLO_EMAIL,
            "to": customer_email,
            "subject": message_metadata["subject"],
            "html": message_metadata["content"],
        }
    )

    if r["id"]:
        logger.info(f"Email sent successfully to {customer_email} with ID: {r["id"]}")


def send_invalid_payment_email(session_details: DictWithStringKeys, cost_in_pence: int):
    message_metadata = _create_invalid_payment_email_message(
        session_details, cost_in_pence
    )

    logger.info(
        f"Sending invalid payment email to management for session_details={session_details}"
    )

    r: resend.Emails.SendResponse = resend.Emails.send(
        {
            "from": HELLO_EMAIL,
            "to": FOUNDER_EMAILS,
            "subject": message_metadata["subject"],
            "html": message_metadata["content"],
        }
    )

    if r["id"]:
        logger.info(f"Invalid payment email sent successfully with ID: {r['id']}")
