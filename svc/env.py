import os

from dotenv import load_dotenv

load_dotenv()

stripe_api_key = os.getenv("STRIPE_SECRET")
stripe_webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
email_password = os.getenv("EMAIL_PASSWORD")
