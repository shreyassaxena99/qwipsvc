import argparse
import os

parser = argparse.ArgumentParser()
parser.add_argument("--supabase-url", help="Supabase URL (overrides SUPABASE_URL)")
parser.add_argument("--supabase-key", help="Supabase key (overrides SUPABASE_KEY)")
parser.add_argument("--jwt-secret", help="JWT secret key (overrides JWT_SECRET_KEY)")
args = parser.parse_args()

if args.supabase_url:
    os.environ["SUPABASE_URL"] = args.supabase_url
if args.supabase_key:
    os.environ["SUPABASE_KEY"] = args.supabase_key
if args.jwt_secret:
    os.environ["JWT_SECRET_KEY"] = args.jwt_secret

from svc.custom_types import TokenScope
from svc.database_accessor import (
    create_supabase_client,
    get_sessions_without_feedback_token,
    save_feedback_token,
)
from svc.jwt_manager import create_jwt_token

supabase = create_supabase_client()
sessions = get_sessions_without_feedback_token(supabase)

print(f"Found {len(sessions)} sessions without a feedback token")

for session in sessions:
    session_id = session["id"]
    feedback_token = create_jwt_token(
        {TokenScope.FEEDBACK.value: {"session_id": session_id}}, TokenScope.FEEDBACK
    )
    save_feedback_token(supabase, session_id, feedback_token)
    print(f"Saved feedback token for session {session_id}")

print("Done")
