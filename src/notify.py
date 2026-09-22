"""
Task 5 — Deadline Notifications & Missing Snapshot Alerts
============================================================
Two checks, both emailed if triggered:

  1. Scans the archive for tenders whose submission deadline is coming up
     within the next N days.
  2. Scans data/processed/ for any missing daily final_<date>.csv snapshot
     between the earliest one on file and today.
"""

import os
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

SMTP_SENDER_EMAIL = os.getenv("SMTP_SENDER_EMAIL")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")
NOTIFY_RECIPIENT_EMAIL = os.getenv("NOTIFY_RECIPIENT_EMAIL")

DEADLINE_COLUMN = "last_offer_presentation_date"
DAYS_AHEAD = 5

PROCESSED_DIR = Path("../data/processed")


# ── 1. Upcoming deadlines ─────────────────────────────────────────────────
def check_upcoming_deadlines(archive_df, days_ahead=DAYS_AHEAD):
    """Returns rows whose deadline falls within the next `days_ahead` days."""
    df = archive_df.copy()
    df[DEADLINE_COLUMN] = pd.to_datetime(df[DEADLINE_COLUMN], errors="coerce")

    today = pd.Timestamp(date.today())
    cutoff = today + timedelta(days=days_ahead)

    upcoming = df[(df[DEADLINE_COLUMN] >= today) & (df[DEADLINE_COLUMN] <= cutoff)]
    return upcoming.sort_values(DEADLINE_COLUMN)


def send_deadline_email(upcoming_df):
    """Builds a simple HTML summary and emails it. No-op if the list is empty."""
    if upcoming_df.empty:
        print("No upcoming deadlines within the window — no email sent.")
        return

    rows_html = "".join(
        f"<tr><td>{row.tender_name}</td><td>{row.agency_name}</td>"
        f"<td>{row[DEADLINE_COLUMN].date()}</td></tr>"
        for _, row in upcoming_df.iterrows()
    )
    body = f"""
    <h3>SEEK — Tenders closing within {DAYS_AHEAD} days</h3>
    <table border="1" cellpadding="6">
      <tr><th>Tender</th><th>Agency</th><th>Deadline</th></tr>
      {rows_html}
    </table>
    """

    msg = MIMEMultipart()
    msg["Subject"] = f"SEEK: {len(upcoming_df)} tender(s) closing soon"
    msg["From"] = SMTP_SENDER_EMAIL
    msg["To"] = NOTIFY_RECIPIENT_EMAIL
    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(SMTP_SENDER_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_SENDER_EMAIL, NOTIFY_RECIPIENT_EMAIL, msg.as_string())

    print(f"Sent deadline email for {len(upcoming_df)} tender(s).")


# ── 2. Missing daily snapshots ────────────────────────────────────────────
def find_missing_snapshot_dates():
    """Compares the expected daily date range (earliest snapshot -> today)
    against the actual final_<date>.csv files present, and returns any
    missing dates."""
    existing_files = sorted(PROCESSED_DIR.glob("final_*.csv"))
    if not existing_files:
        return []

    existing_dates = set()
    for f in existing_files:
        date_str = f.stem.replace("final_", "")
        try:
            existing_dates.add(date.fromisoformat(date_str))
        except ValueError:
            continue

    earliest = min(existing_dates)
    today = date.today()
    expected_dates = {earliest + timedelta(days=i) for i in range((today - earliest).days + 1)}

    missing = sorted(expected_dates - existing_dates)
    if missing:
        print(f"WARNING: {len(missing)} missing daily snapshot(s): {missing}")
    else:
        print("No missing daily snapshots.")
    return missing


def send_missing_snapshot_alert(missing_dates):
    """Emails a summary of missing snapshot dates. No-op if none are missing."""
    if not missing_dates:
        return

    body = f"""
    <h3>SEEK — Missing Daily Snapshots Detected</h3>
    <p>The following dates have no final_&lt;date&gt;.csv file:</p>
    <ul>{"".join(f"<li>{d}</li>" for d in missing_dates)}</ul>
    """

    msg = MIMEMultipart()
    msg["Subject"] = f"SEEK: {len(missing_dates)} missing snapshot(s)"
    msg["From"] = SMTP_SENDER_EMAIL
    msg["To"] = NOTIFY_RECIPIENT_EMAIL
    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(SMTP_SENDER_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_SENDER_EMAIL, NOTIFY_RECIPIENT_EMAIL, msg.as_string())

    print(f"Sent missing-snapshot alert for {len(missing_dates)} date(s).")


# ── 3. Entry point ─────────────────────────────────────────────────────────
def main():
    archive_path = PROCESSED_DIR / "tenders_archive.csv"
    archive_df = pd.read_csv(archive_path)

    upcoming = check_upcoming_deadlines(archive_df)
    send_deadline_email(upcoming)

    missing = find_missing_snapshot_dates()
    send_missing_snapshot_alert(missing)


if __name__ == "__main__":
    main()