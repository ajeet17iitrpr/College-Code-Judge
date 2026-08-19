from __future__ import annotations

import argparse
import secrets

from app.services.mailer import send_otp_email


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a College Code Judge SMTP test OTP")
    parser.add_argument("email")
    args = parser.parse_args()
    otp = f"{secrets.randbelow(900000) + 100000:06d}"
    send_otp_email(to_email=args.email, otp=otp, purpose="REGISTRATION")
    print(f"Test OTP email sent to {args.email}.")


if __name__ == "__main__":
    main()
