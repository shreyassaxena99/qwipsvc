import os

# Set required env vars before any svc module is imported.
# seam_accessor.py calls Seam() at module load time as a default arg,
# so SEAM_API_KEY must be present before any svc import chain runs.
os.environ.setdefault("SEAM_API_KEY", "seam_sk_test_fake_key_0000000000000000000")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-for-unit-tests-only")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-service-role-key")
os.environ.setdefault("STRIPE_SECRET", "sk_test_fake_stripe_key")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test_fake_webhook_secret")
# 32 null bytes as base64url — a valid AES-256 key for StaticCodeManager tests
os.environ.setdefault(
    "STATIC_CODE_B64_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)
