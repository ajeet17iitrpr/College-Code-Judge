from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from ..config import settings


class MailDeliveryError(RuntimeError):
    pass


def send_otp_email(*, to_email: str, otp: str, purpose: str) -> None:
    if not settings.smtp_username or not settings.smtp_password:
        raise MailDeliveryError("SMTP credentials are not configured.")

    if purpose == "REGISTRATION":
        subject = "College Code Judge registration OTP"
        heading = "Registration verification"
        action = "complete your student registration"
    elif purpose == "PASSWORD_RESET":
        subject = "College Code Judge password reset OTP"
        heading = "Password reset verification"
        action = "reset your password"
    else:
        raise ValueError("Unsupported OTP purpose")

    text = (
        f"{heading}\n\n"
        f"Your one-time verification code is: {otp}\n\n"
        f"Use this code to {action}. The code expires in {settings.otp_expiry_minutes} minutes.\n"
        "If you did not request this code, you can ignore this email.\n"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    msg.set_content(text)

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.ehlo()
            if settings.smtp_starttls:
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
    except Exception as exc:  # avoid leaking SMTP credentials/details to clients
        raise MailDeliveryError("Unable to send verification email.") from exc


def send_result_email(*, to_email: str, student_name: str, rows: list[dict], summary: dict, pdf_bytes: bytes) -> None:
    if not settings.smtp_username or not settings.smtp_password:
        raise MailDeliveryError("SMTP credentials are not configured.")

    lines = [
        f"Hello {student_name},",
        "",
        f"Your result for {summary.get('exam_name', 'the programming assessment')} is attached as a PDF.",
        "",
    ]
    for row in rows:
        lines.append(
            f"{row['problem']}: {row['final_score']:.2f}/{row['maximum']:.2f} "
            f"({row.get('language') or 'not attempted'}, {row['verdict']})"
        )
    lines.extend(
        [
            "",
            f"Total: {summary['total']:.2f}/{summary['maximum']:.2f}",
            f"Percentage: {summary['percentage']:.2f}%",
            f"Result: {'PASS' if summary['passed'] else 'FAIL'}",
            "",
            "Regards,",
            settings.institute_name,
        ]
    )

    msg = EmailMessage()
    msg["Subject"] = f"Result - {summary.get('exam_name', 'Programming Assessment')}"
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    msg.set_content("\n".join(lines))
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename="result.pdf")

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            if settings.smtp_starttls:
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
    except Exception as exc:
        raise MailDeliveryError("Unable to send result email.") from exc
