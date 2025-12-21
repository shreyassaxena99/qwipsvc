import logging

import resend

from svc.env import resend_api_key
from svc.models import SessionDetails
from svc.utils import format_datetime_for_email

HELLO_EMAIL = "shreyas@qwip.co.uk"

resend.api_key = resend_api_key

logger = logging.getLogger(__name__)


def _create_email_message(session: SessionDetails) -> dict[str, str]:
    formatted_start_time = format_datetime_for_email(session.start_time.isoformat())
    subject = f"Your Qwip Session at {session.pod_name} from {formatted_start_time}"

    content = f"""
<html>
  <body style="font-family: Arial, sans-serif; background-color: #FAFAF8; padding: 20px; margin: 0;">
    <div style="max-width: 600px; margin: auto; background-color: #ffffff; padding: 30px; border-radius: 8px;">
      <h2 style="color: #1f3d32;">Thanks for booking with Qwip!</h2>
      <p>Your session details are shown below:</p>

      <p><strong>Start Time:</strong> {formatted_start_time}</p>
      <p><strong>Access Code:</strong> {session.access_code}</p>

      <p>To access your workspace, please go to <strong>{session.address}</strong> and enter your access code on the pod's keypad.</p>

      <p><strong>Important:</strong> After typing your code, press the <strong>Yale button</strong> to confirm and unlock the door.</p>

      <p>Click the button below to see how much your session is costing and end your session:</p>

      <a href="https://qwip.co.uk/session?t={session.session_token}"
         style="display:inline-block; padding:12px 20px; margin-top:15px; background-color:#1f3d32; color:#ffffff; text-decoration:none; border-radius:6px; font-weight:bold;">
        Manage Your Session
      </a>

      <p style="margin-top: 30px;">Thank you for using Qwip!</p>

      <hr style="border: none; border-top: 1px solid #eee; margin: 40px 0;" />

      <p style="font-size: 12px; color: #777;">
        © 2025 Qwip Ltd. All rights reserved.<br />
        <a href="https://qwip.co.uk/privacy" style="color: #777; text-decoration: underline;">Privacy Policy</a> | 
        <a href="https://qwip.co.uk/contact" style="color: #777; text-decoration: underline;">Contact Us</a><br />
        Qwip Ltd, 128 City Road, London, EC1V 2NX
      </p>
    </div>
  </body>
</html>
"""

    return {"subject": subject, "content": content}


def send_access_email(customer_email: str, booking: SessionDetails):
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
