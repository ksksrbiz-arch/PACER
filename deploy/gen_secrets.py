"""Generate the operator-side secrets needed to bootstrap the VPS.

Writes deploy/.secrets-staging/secrets.env (gitignored, mode 0600) with:
  - API_AUTH_TOKEN     (96 hex chars, for the future REST API if/when added)
  - POSTGRES_PASSWORD  (48 hex chars, for the production Postgres user)
  - PACER_BASIC_USER   ('keith')
  - PACER_BASIC_PASS   (20-char alnum, the human-memorable login)
  - PACER_BASIC_HASH   (bcrypt cost=14 of PACER_BASIC_PASS, used by Caddy)

Run once before `deploy/remote_bootstrap.sh`. Re-running rotates everything.
"""
from __future__ import annotations

import os
import secrets
import string
import subprocess
import sys
from pathlib import Path


def ensure_bcrypt() -> None:
    try:
        import bcrypt  # noqa: F401
        return
    except ImportError:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--break-system-packages",
                "bcrypt",
            ]
        )


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    out_dir = repo / "deploy" / ".secrets-staging"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "secrets.env"

    api_auth_token = secrets.token_hex(48)
    postgres_password = secrets.token_hex(24)
    basic_user = "keith"
    alphabet = string.ascii_letters + string.digits
    basic_pass = "".join(secrets.choice(alphabet) for _ in range(20))

    ensure_bcrypt()
    import bcrypt

    basic_hash = bcrypt.hashpw(basic_pass.encode(), bcrypt.gensalt(rounds=14)).decode()

    body = (
        f"API_AUTH_TOKEN={api_auth_token}\n"
        f"POSTGRES_PASSWORD={postgres_password}\n"
        f"PACER_BASIC_USER={basic_user}\n"
        f"PACER_BASIC_PASS={basic_pass}\n"
        f"PACER_BASIC_HASH={basic_hash}\n"
    )
    out_path.write_text(body)
    os.chmod(out_path, 0o600)

    print(f"wrote {out_path} ({out_path.stat().st_size} bytes, mode 0600)")
    print()
    print("=== for the operator (save these somewhere safe) ===")
    print(f"  Login user:     {basic_user}")
    print(f"  Login password: {basic_pass}")
    print()
    print("=== values landing in production .env ===")
    print(f"  API_AUTH_TOKEN     = {api_auth_token[:8]}...{api_auth_token[-8:]}")
    print(f"  POSTGRES_PASSWORD  = {postgres_password[:6]}...{postgres_password[-6:]}")
    print(f"  PACER_BASIC_HASH   = {basic_hash[:7]}...{basic_hash[-6:]}")


if __name__ == "__main__":
    main()
