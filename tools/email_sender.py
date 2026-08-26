"""
Transactional email — currently just the magic-link login.

Called by `app.request_magic_link`. When the send succeeds the link goes only to
the recipient's inbox; when it fails — or when sending is off — the caller prints
the link to the server console instead, which is the local-development path.

SENDING IS OFF UNTIL EXPLICITLY TURNED ON. `EMAIL_SENDING_ENABLED` must be truthy
*and* RESEND_API_KEY set before anything leaves the machine. The switch exists so
that "paused" is a decision on the record rather than a side effect of a missing
key: without it, the day a key is added to try something out, real mail starts
going to real people with nothing else having changed.

Provider is Resend: free tier is 3,000 emails/month and 100/day, which is far
past anything this needs, and the API is a single JSON POST so there's no SDK to
add. Set RESEND_API_KEY and EMAIL_FROM to switch it on.

Degrading without a key is the normal path, not an error case. With no key the
link is logged and printed to the console exactly as before, and the caller is
told delivery didn't happen so it can say so honestly in the UI. Nothing raises:
a login attempt must never 500 because an email provider is down or unconfigured.

Sending domain: Resend will only send from a domain you have verified. Until one
is, `onboarding@resend.dev` works for delivery to your OWN address, which is
enough to see the mail but not to serve real users.
"""
import html
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://api.resend.com/emails"
_TIMEOUT = 10.0  # a login request waits on this — fail fast, never hang

_DEFAULT_FROM = "Deal Scout <onboarding@resend.dev>"
_LINK_TTL_MINUTES = 15  # must match create_auth_token()


# Anything else — unset, "", "false", "0" — leaves sending off. The default is
# off in both directions: a typo'd value pauses mail rather than releasing it.
_TRUTHY = {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    """False unless EMAIL_SENDING_ENABLED explicitly turns sending on."""
    return os.getenv("EMAIL_SENDING_ENABLED", "").strip().lower() in _TRUTHY


def is_configured() -> bool:
    """
    True when a real send is possible: switched on *and* holding a key.

    False means console-only delivery, which is what the sign-in box tells the
    user to expect.
    """
    return is_enabled() and bool(os.getenv("RESEND_API_KEY"))


def _sender() -> str:
    return os.getenv("EMAIL_FROM") or _DEFAULT_FROM


def render_magic_link_email(link: str, email: str = "") -> tuple[str, str]:
    """
    Build the (html, plain_text) bodies for a login link.

    Both are returned because a text/plain alternative measurably helps spam
    scoring, and some clients show it instead of the HTML.

    The link is escaped for the href even though we generate it ourselves — the
    token is URL-safe, but the base URL comes from BASE_URL, which is operator
    input rather than a constant.
    """
    safe_link = html.escape(link, quote=True)
    safe_email = html.escape(email)
    to_line = (
        f"<p style=\"margin:0 0 24px;font-size:14px;color:#6b7280;\">"
        f"Requested for {safe_email}.</p>" if email else ""
    )

    html_body = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f3f4f6;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#f3f4f6;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:480px;background:#ffffff;border:1px solid #e5e7eb;
                    border-radius:12px;padding:32px;
                    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
        <tr><td>
          <p style="margin:0 0 4px;font-size:13px;font-weight:600;color:#16a34a;
                    letter-spacing:.04em;text-transform:uppercase;">Deal Scout</p>
          <h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;color:#111827;">
            Your sign-in link
          </h1>
          <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#111827;">
            Click the button below to sign in. No password needed.
          </p>
          <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
            <tr><td style="border-radius:6px;background:#16a34a;">
              <a href="{safe_link}"
                 style="display:inline-block;padding:12px 24px;font-size:15px;
                        font-weight:700;color:#ffffff;text-decoration:none;">
                Sign in to Deal Scout
              </a>
            </td></tr>
          </table>
          {to_line}
          <p style="margin:0 0 8px;font-size:13px;line-height:1.6;color:#6b7280;">
            This link expires in {_LINK_TTL_MINUTES} minutes and can only be used once.
            If you didn't request it, you can ignore this email — nobody can get in
            without the link.
          </p>
          <p style="margin:24px 0 0;padding-top:16px;border-top:1px solid #e5e7eb;
                    font-size:12px;line-height:1.6;color:#6b7280;word-break:break-all;">
            Button not working? Paste this into your browser:<br>
            <span style="color:#2563eb;">{safe_link}</span>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    text_body = (
        "Your sign-in link for Deal Scout\n\n"
        f"{link}\n\n"
        f"This link expires in {_LINK_TTL_MINUTES} minutes and can only be used once.\n"
        "If you didn't request it, you can ignore this email."
    )
    return html_body, text_body


def send_magic_link(to: str, link: str) -> bool:
    """
    Deliver a login link. Returns True only if the provider accepted it.

    Never raises. A False return means the link was logged to the console
    instead, which is the local-development path — and it is what happens while
    EMAIL_SENDING_ENABLED is off, regardless of whether a key is present.
    """
    # Checked before the key, and logged separately, so "why didn't that send?"
    # is answerable from the log line alone.
    if not is_enabled():
        logger.info(
            "Email sending is paused (EMAIL_SENDING_ENABLED is not set) — "
            "magic link goes to the console only"
        )
        return False

    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.info("RESEND_API_KEY not set — magic link goes to the console only")
        return False

    html_body, text_body = render_magic_link_email(link, to)
    try:
        response = httpx.post(
            _API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": _sender(),
                "to": [to],
                "subject": "Your Deal Scout sign-in link",
                "html": html_body,
                "text": text_body,
            },
            timeout=_TIMEOUT,
        )
    except Exception as e:
        # Network failure, DNS, timeout — the console fallback still works.
        logger.warning("Could not reach the email provider: %r", e)
        return False

    if response.status_code >= 400:
        # Body carries the actionable reason (unverified domain, bad key).
        logger.warning(
            "Email provider rejected the send (HTTP %d): %s",
            response.status_code, response.text[:300],
        )
        return False

    logger.info("Magic link emailed to %s", to)
    return True
