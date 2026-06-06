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
    subject = f"Your qwip session at {session.pod_name} from {formatted_start_time}"
    spaced_code = " ".join(str(session.access_code))

    content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>qwip</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Funnel+Display:wght@400;700&family=Cormorant+Garamond:ital@1&family=DM+Sans:wght@400;500;600;700&display=swap');
    body {{ margin: 0; padding: 0; background-color: #f2f0ec; font-family: 'DM Sans', Arial, sans-serif; color: #1f3d32; -webkit-font-smoothing: antialiased; }}
    .outer {{ padding: 40px 16px; }}
    .card {{ max-width: 560px; margin: 0 auto; background: #FDFBF7; border-radius: 12px; overflow: hidden; }}
    .inner {{ padding: 44px 40px 36px; }}
    .logo {{ font-family: 'Funnel Display', sans-serif; font-size: 32px; font-weight: 700; color: #1f3d32; text-decoration: none; display: block; margin-bottom: 28px; }}
    .divider {{ height: 1px; background: #c8ddd2; margin-bottom: 32px; }}
    h1 {{ font-family: 'Funnel Display', sans-serif; font-size: 26px; font-weight: 700; margin: 0 0 12px; color: #1f3d32; line-height: 1.2; }}
    p {{ font-size: 15px; line-height: 1.7; color: #4a6b5c; margin: 0 0 16px; }}
    .code-block {{ font-family: 'Funnel Display', sans-serif; font-size: 36px; font-weight: 700; letter-spacing: 8px; color: #1f3d32; margin: 28px 0; text-align: center; background: #f0ede7; border-radius: 10px; padding: 20px; }}
    .btn {{ display: inline-block; background: #1f3d32; color: #ffffff !important; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; font-family: 'DM Sans', sans-serif; }}
    .tagline {{ margin-top: 28px; font-size: 14px; color: #6b8f7e; }}
    .accent {{ font-style: italic; font-family: 'Cormorant Garamond', Georgia, serif; font-size: 16px; }}
    .footer-bar {{ background: #1f3d32; padding: 24px 40px; }}
    .footer-bar p {{ color: #a3c4b3; font-size: 12px; margin: 0 0 4px; }}
    .footer-bar a {{ color: #a3c4b3; text-decoration: underline; }}
    .footer-bar .brand {{ font-family: 'Funnel Display', sans-serif; color: #ffffff; font-size: 16px; font-weight: 700; margin-bottom: 8px; }}
  </style>
</head>
<body>
  <div class="outer">
    <div class="card">
      <div class="inner">
        <a href="https://qwip.co.uk" class="logo">qwip</a>
        <div class="divider"></div>
        <h1>your pod is ready</h1>
        <p>Hi there — your session at <strong style="color:#1f3d32;">{session.pod_name}</strong> starting at <strong style="color:#1f3d32;">{formatted_start_time}</strong> is confirmed and ready to go.</p>
        <p>Head to <strong style="color:#1f3d32;">{session.address}</strong>, and use the access code below.</p>
        <div class="code-block">{spaced_code}</div>
        <div style="text-align:center; margin: 28px 0;">
          <a href="https://qwip.co.uk/session?t={session.session_token}" class="btn">Manage Session</a>
        </div>
        <p class="tagline">quiet, on demand. <span class="accent">anywhere.</span></p>
      </div>
      <div class="footer-bar">
        <p class="brand">qwip</p>
        <p>Private, soundproof pods in the places you already are.</p>
        <p style="margin-top: 12px;"><a href="https://qwip.co.uk/privacy-policy">Privacy Policy</a>&nbsp;&nbsp;·&nbsp;&nbsp;<a href="https://qwip.co.uk/support">Contact Us</a></p>
      </div>
    </div>
  </div>
</body>
</html>"""

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
          <a href="https://qwip.co.uk/privacy-policy" style="color: #777; text-decoration: underline;">Privacy Policy</a> |
          <a href="https://qwip.co.uk/support" style="color: #777; text-decoration: underline;">Contact Us</a><br />
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
