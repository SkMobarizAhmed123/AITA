# ──────────────────────────────────────────────────────────────────────────────
# gmail_api.py — Gmail access via OAuth2 (no passwords, no browser bot)
#
# Combines what used to be two files:
#   - gmail_api.py       (raw Gmail API calls: list, send, ...)
#   - gmail_commands.py  (natural-language command parsing)
# into one module so main.py has a single, consistent import surface:
#
#     import gmail_api
#     gmail_api.parse_gmail_command(message)
#
# One-time setup:
#   1. Go to https://console.cloud.google.com/apis/credentials
#   2. Create an OAuth 2.0 Client ID -> "Desktop app"
#   3. Download the JSON, save it as credentials.json next to this file
#   4. pip install google-auth-oauthlib google-api-python-client google-auth
#   5. Run: python gmail_api.py --setup
#      A browser window opens once, asking you to approve access to YOUR
#      OWN Gmail account. After approving, a token.json is saved and every
#      future run reuses it silently (auto-refreshed).
#
#   NOTE: if you already have a token.json from the old readonly+send-only
#   scope list, delete it and re-run --setup once after upgrading this file
#   — the new scopes (modify) require re-consent.
# ──────────────────────────────────────────────────────────────────────────────

import os
import re
import base64
from email.mime.text import MIMEText
from typing import List, Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",   # star/unstar, trash/untrash, labels
    "https://www.googleapis.com/auth/gmail.compose",  # drafts
]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

_service = None  # cached client


# ──────────────────────────────────────────────────────────────────────────────
#  Auth / low-level client
# ──────────────────────────────────────────────────────────────────────────────
def _get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise RuntimeError(
                    f"Missing {CREDENTIALS_FILE}. Download it from Google Cloud "
                    "Console (OAuth client, Desktop app) and place it next to this file."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def get_service():
    global _service
    if _service is None:
        _service = build("gmail", "v1", credentials=_get_credentials())
    return _service


def _headers_dict(msg: Dict) -> Dict[str, str]:
    return {h["name"]: h["value"] for h in msg["payload"]["headers"]}


# ──────────────────────────────────────────────────────────────────────────────
#  Core operations
# ──────────────────────────────────────────────────────────────────────────────
def list_unread(max_results: int = 5) -> List[Dict]:
    service = get_service()
    resp = service.users().messages().list(
        userId="me", labelIds=["UNREAD", "INBOX"], maxResults=max_results
    ).execute()
    return _hydrate(resp.get("messages", []))


def get_unread_count() -> int:
    service = get_service()
    label = service.users().labels().get(userId="me", id="UNREAD").execute()
    return label.get("messagesUnread", 0)


def search_emails(query: str, max_results: int = 5) -> List[Dict]:
    """Gmail search syntax: from:, subject:, has:attachment, is:unread, etc."""
    service = get_service()
    resp = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    return _hydrate(resp.get("messages", []))


def _hydrate(message_refs: List[Dict]) -> List[Dict]:
    service = get_service()
    results = []
    for m in message_refs:
        msg = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date", "Message-ID", "References"],
        ).execute()
        headers = _headers_dict(msg)
        results.append({
            "id": m["id"],
            "threadId": msg["threadId"],
            "subject": headers.get("Subject", "(no subject)"),
            "from": headers.get("From", "unknown"),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
        })
    return results


def find_latest_from(sender_or_keyword: str) -> Optional[Dict]:
    """Resolve 'the latest email from X' / 'the email about X' to a single message."""
    results = search_emails(f'from:{sender_or_keyword}', max_results=1)
    if not results:
        results = search_emails(sender_or_keyword, max_results=1)
    return results[0] if results else None


def _confirm_action(action_desc: str) -> bool:
    print(f"\n⚠️  SECURITY WARNING: You are about to {action_desc}.")
    resp = input("Are you sure you want to proceed? (y/N): ")
    return resp.strip().lower() in ['y', 'yes']

def send_email(to: str, subject: str, body: str) -> str:
    if not _confirm_action(f"send an email to {to}"):
        return "❌ Action cancelled by user."
    service = get_service()
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"✅ Email sent to {to}"


def reply_to_email(message_id: str, body: str, reply_all: bool = False) -> str:
    if not _confirm_action(f"reply to email ID {message_id}"):
        return "❌ Action cancelled by user."
    service = get_service()
    original = service.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["Subject", "From", "To", "Cc", "Message-ID", "References"],
    ).execute()
    headers = _headers_dict(original)
    subject = headers.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    to = headers.get("From", "")
    cc = headers.get("Cc", "") if reply_all else ""

    reply = MIMEText(body)
    reply["to"] = to
    if cc:
        reply["cc"] = cc
    reply["subject"] = subject
    reply["In-Reply-To"] = headers.get("Message-ID", "")
    refs = headers.get("References", "")
    reply["References"] = f'{refs} {headers.get("Message-ID", "")}'.strip()

    raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
    service.users().messages().send(
        userId="me", body={"raw": raw, "threadId": original["threadId"]}
    ).execute()
    return f"✅ Replied to {to}"


def forward_email(message_id: str, to: str, note: str = "") -> str:
    if not _confirm_action(f"forward email ID {message_id} to {to}"):
        return "❌ Action cancelled by user."
    service = get_service()
    original = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    headers = _headers_dict(original)
    subject = headers.get("Subject", "")
    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"
    original_snippet = original.get("snippet", "")
    body = f"{note}\n\n---------- Forwarded message ----------\n{original_snippet}"

    forward = MIMEText(body)
    forward["to"] = to
    forward["subject"] = subject
    raw = base64.urlsafe_b64encode(forward.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"✅ Forwarded to {to}"


def create_draft(to: str, subject: str, body: str) -> str:
    service = get_service()
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return f"✅ Draft saved for {to}"


def _modify_labels(message_id: str, add: List[str] = None, remove: List[str] = None) -> None:
    service = get_service()
    body = {}
    if add:
        body["addLabelIds"] = add
    if remove:
        body["removeLabelIds"] = remove
    service.users().messages().modify(userId="me", id=message_id, body=body).execute()


def star_email(message_id: str) -> str:
    _modify_labels(message_id, add=["STARRED"])
    return "✅ Starred"


def unstar_email(message_id: str) -> str:
    _modify_labels(message_id, remove=["STARRED"])
    return "✅ Unstarred"


def trash_email(message_id: str) -> str:
    if not _confirm_action(f"move email ID {message_id} to trash"):
        return "❌ Action cancelled by user."
    service = get_service()
    service.users().messages().trash(userId="me", id=message_id).execute()
    return "✅ Moved to Trash"


def untrash_email(message_id: str) -> str:
    service = get_service()
    service.users().messages().untrash(userId="me", id=message_id).execute()
    return "✅ Restored from Trash"


def mark_spam(message_id: str) -> str:
    if not _confirm_action(f"mark email ID {message_id} as spam"):
        return "❌ Action cancelled by user."
    _modify_labels(message_id, add=["SPAM"], remove=["INBOX"])
    return "✅ Marked as spam"


def format_unread_summary(max_results: int = 5) -> str:
    emails = list_unread(max_results=max_results)
    if not emails:
        return "📭 No unread emails."
    lines = [f"📧 You have {len(emails)} unread email(s):"]
    for e in emails:
        lines.append(f"• From {e['from']} — \"{e['subject']}\"\n  {e['snippet']}")
    return "\n".join(lines)


def format_search_results(query: str, max_results: int = 5) -> str:
    emails = search_emails(query, max_results=max_results)
    if not emails:
        return f"🔍 No emails found for '{query}'."
    lines = [f"🔍 Found {len(emails)} email(s) for '{query}':"]
    for e in emails:
        lines.append(f"• From {e['from']} — \"{e['subject']}\"\n  {e['snippet']}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
#  Natural-language command parser (replaces old gmail_commands.py)
# ──────────────────────────────────────────────────────────────────────────────
def _extract_quoted(message: str) -> List[str]:
    return re.findall(r'["\']([^"\']+)["\']', message)


def _extract_email_addr(message: str) -> Optional[str]:
    m = re.search(r'[\w\.\-]+@[\w\.\-]+', message)
    return m.group(0) if m else None


def parse_gmail_command(message: str) -> Optional[str]:
    """
    Handles the Gmail commands that aren't already covered by main.py's
    direct send-email regex. Returns None if nothing matched, so callers
    can fall through to a generic help message.

    Supported phrasings (examples):
      "read my unread emails" / "check my inbox" / "unread count"
      "search emails from john about invoice"
      "reply to the latest email from john saying thanks, see you then"
      "forward the latest email from john to jane@example.com"
      "star the latest email from john"
      "unstar the email from john"
      "delete the latest email from john" / "trash ..."
      "mark the email from john as spam"
      "draft an email to jane@example.com subject Hi saying let's talk"
    """
    msg = message.strip()
    low = msg.lower()

    try:
        # ── Unread / inbox summary ──
        if any(kw in low for kw in ["unread count", "how many unread"]):
            return f"📬 You have {get_unread_count()} unread email(s)."
        if any(kw in low for kw in ["unread", "check my inbox", "check inbox", "read my email"]):
            return format_unread_summary(max_results=5)

        # ── Search ──
        search_match = re.search(r'search emails?\s+(.+)', low)
        if search_match:
            return format_search_results(search_match.group(1).strip(), max_results=5)

        # ── Reply ──
        reply_match = re.search(
            r'reply to\s+(?:the\s+)?(?:latest\s+)?email\s+from\s+(.+?)\s+saying\s+(.+)',
            low, re.IGNORECASE,
        )
        if reply_match:
            sender, body = reply_match.groups()
            target = find_latest_from(sender.strip())
            if not target:
                return f"❌ Couldn't find an email from '{sender.strip()}'."
            reply_all = "reply all" in low or "everyone" in low
            return reply_to_email(target["id"], body.strip(), reply_all=reply_all)

        # ── Forward ──
        fwd_match = re.search(
            r'forward\s+(?:the\s+)?(?:latest\s+)?email\s+from\s+(.+?)\s+to\s+([\w\.\-]+@[\w\.\-]+)',
            msg, re.IGNORECASE,
        )
        if fwd_match:
            sender, to = fwd_match.groups()
            target = find_latest_from(sender.strip())
            if not target:
                return f"❌ Couldn't find an email from '{sender.strip()}'."
            return forward_email(target["id"], to.strip())

        # ── Star / Unstar ──
        if "star" in low and "email" in low:
            m = re.search(r'(?:un)?star\s+(?:the\s+)?(?:latest\s+)?email\s+from\s+(.+)', low)
            if m:
                target = find_latest_from(m.group(1).strip())
                if not target:
                    return f"❌ Couldn't find an email from '{m.group(1).strip()}'."
                return unstar_email(target["id"]) if low.startswith("unstar") or "unstar" in low else star_email(target["id"])

        # ── Delete / Trash ──
        del_match = re.search(
            r'(?:delete|trash|remove)\s+(?:the\s+)?(?:latest\s+)?email\s+from\s+(.+)', low
        )
        if del_match:
            target = find_latest_from(del_match.group(1).strip())
            if not target:
                return f"❌ Couldn't find an email from '{del_match.group(1).strip()}'."
            return trash_email(target["id"])

        # ── Mark as spam ──
        spam_match = re.search(r'(?:mark\s+)?(?:the\s+)?email\s+from\s+(.+?)\s+as\s+spam', low)
        if spam_match:
            target = find_latest_from(spam_match.group(1).strip())
            if not target:
                return f"❌ Couldn't find an email from '{spam_match.group(1).strip()}'."
            return mark_spam(target["id"])

        # ── Draft ──
        draft_match = re.search(
            r'draft\s+(?:an?\s+)?email\s+to\s+([\w\.\-]+@[\w\.\-]+)\s+'
            r'(?:with\s+)?subject\s+["\']?(.+?)["\']?\s+(?:saying|body|:)\s+["\']?(.+)["\']?$',
            msg, re.IGNORECASE,
        )
        if draft_match:
            to, subject, body = draft_match.groups()
            return create_draft(to.strip(), subject.strip(), body.strip())

        return None

    except HttpError as e:
        return f"❌ Gmail API error: {e}"
    except RuntimeError as e:
        return f"❌ Gmail not set up: {e}"
    except Exception as e:
        return f"❌ Gmail command failed: {e}"


if __name__ == "__main__":
    import sys
    if "--setup" in sys.argv:
        get_service()
        print("✅ Gmail API authorized. token.json saved — future runs won't prompt again.")
    else:
        print("Usage: python gmail_api.py --setup")