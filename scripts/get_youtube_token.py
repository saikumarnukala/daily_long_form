"""
One-time helper script to obtain a YouTube OAuth2 refresh token.

Run this ONCE on your local machine (requires a browser).
The printed refresh_token value should be stored in:
  - Your .env file as YOUTUBE_REFRESH_TOKEN=<value>
  - GitHub Secrets as YOUTUBE_REFRESH_TOKEN for CI/CD runs

Usage:
    python scripts/get_youtube_token.py

Prerequisites:
    pip install google-auth-oauthlib
    A client_secret.json file downloaded from Google Cloud Console.
    (Or set YOUTUBE_CLIENT_SECRET env var with the JSON content.)
"""
import json
import os
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("ERROR: google-auth-oauthlib is not installed.")
    print("Run: pip install google-auth-oauthlib")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def main():
    # Try to read client secret from file first, then env var
    client_secret_file = "client_secret.json"
    client_secret_env = os.getenv("YOUTUBE_CLIENT_SECRET", "")

    if os.path.exists(client_secret_file):
        print(f"Using client secret file: {client_secret_file}")
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    elif client_secret_env:
        print("Using YOUTUBE_CLIENT_SECRET environment variable.")
        client_config = json.loads(client_secret_env)
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    else:
        print("ERROR: No client secret found.")
        print()
        print("Option A: Download client_secret.json from Google Cloud Console and")
        print("          place it in the project root directory.")
        print()
        print("Option B: Set YOUTUBE_CLIENT_SECRET environment variable with the")
        print("          full JSON content of your client secret.")
        print()
        print("Steps to get client_secret.json:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create a project (or select existing)")
        print("  3. Enable 'YouTube Data API v3' from API Library")
        print("  4. Go to Credentials → Create Credentials → OAuth 2.0 Client ID")
        print("  5. Application type: Desktop app")
        print("  6. Download the JSON file")
        sys.exit(1)

    print("\nA browser window will open for you to authorise the application.")
    print("Log in with the YouTube channel's Google account.\n")

    credentials = flow.run_local_server(port=0)

    print("\n" + "=" * 60)
    print("SUCCESS! Copy the values below into your .env file and GitHub Secrets:")
    print("=" * 60)
    print(f"\nYOUTUBE_REFRESH_TOKEN={credentials.refresh_token}\n")
    print("=" * 60)
    print("\nNote: The refresh token does NOT expire unless you:")
    print("  - Revoke app access in your Google account settings")
    print("  - The app is unused for 6 months (Google may invalidate it)")
    print("\nStore it securely. Treat it like a password.")


if __name__ == "__main__":
    main()
