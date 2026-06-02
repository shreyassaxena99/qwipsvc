import os

from dotenv import load_dotenv

load_dotenv()

stripe_api_key = os.getenv("STRIPE_SECRET")
stripe_webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
resend_api_key = os.getenv("RESEND_API_KEY")
use_static_codes = os.getenv("USE_STATIC_CODES", "false").lower() == "true"
static_code_b64_key = os.getenv("STATIC_CODE_B64_KEY")
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
