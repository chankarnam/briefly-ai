# ingest_meet.py
from __future__ import annotations
import io
from typing import List, Tuple, Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

def get_drive_service(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
):
    """Authenticate and return a Drive API service."""
    creds = None
    try:
        creds = Credentials.from_authorized_user_file(token_path, DRIVE_SCOPES)
    except Exception:
        pass

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, DRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)

def list_meet_transcripts(service, page_size: int = 25) -> List[Tuple[str, str, str]]:
    """
    Return a list of tuples (file_id, name, mimeType) for likely Google Meet transcripts.
    We look for:
      1) Google Docs with 'Transcript' in the name
      2) .vtt caption files (from recordings)
    """
    q_parts = [
        "trashed = false",
        "("
        "  (mimeType = 'application/vnd.google-apps.document' and name contains 'Transcript')"
        "  or (mimeType = 'text/vtt')"
        "  or (name contains '.vtt')"
        ")"
    ]
    q = " and ".join(q_parts)

    resp = service.files().list(
        q=q,
        pageSize=page_size,
        fields="files(id, name, mimeType, modifiedTime)",
        orderBy="modifiedTime desc",
    ).execute()

    files = resp.get("files", [])
    return [(f["id"], f["name"], f["mimeType"]) for f in files]

def export_transcript_text(service, file_id: str, mime_type: str) -> str:
    """
    Export file content as plain text:
      - Google Docs -> export 'text/plain'
      - .vtt -> download and decode
    """
    if mime_type == "application/vnd.google-apps.document":
        request = service.files().export_media(fileId=file_id, mimeType="text/plain")
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue().decode("utf-8", errors="ignore")

    # Fallback: normal file download (e.g., text/vtt)
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    return fh.getvalue().decode("utf-8", errors="ignore")
