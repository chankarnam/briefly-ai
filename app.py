# app_brieflyai.py
import io
import os
import re
import json
from pathlib import Path
from datetime import datetime, date
import typing as t

import pandas as pd
import streamlit as st

# --- Google APIs (optional sections are guarded) ---
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.auth.transport.requests import Request  # for token refresh

# --- LLM (Ollama local model) ---
from langchain_ollama import OllamaLLM

# ===================================================
# App setup
# ===================================================
st.set_page_config(page_title="brieflyAI — Meeting Actions, Fast", layout="wide", initial_sidebar_state="collapsed")

# --- THEME TOGGLE ---
theme = st.radio("Theme", ["Light", "Dark"], horizontal=True, index=0)

def inject_theme_css(mode: str):
    if mode == "Dark":
        st.markdown("""
        <style>
          body, .main, .block-container { background:#0c0c0c; color:#f2f2f2; }
          h1,h2,h3,h4,h5,h6, .stMarkdown p { color:#ffffff !important; }
          .stDataFrame { filter: brightness(0.98); }
          hr { border-color:#2b2b2b !important; }
        </style>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <style>
          body, .main, .block-container { background:#ffffff; color:#1c1c1c; }
          h1,h2,h3,h4,h5,h6, .stMarkdown p { color:#111111 !important; }
          hr { border-color:#ececec !important; }
        </style>
        """, unsafe_allow_html=True)

inject_theme_css(theme)

# --- BANNER (auto picks dark/light) ---
banner_light = Path("light_brieflyai.png")
banner_dark  = Path("dark_brieflyai.png")
banner_path  = banner_dark if theme == "Dark" and banner_dark.exists() else banner_light

st.markdown("<div style='margin: 0.75rem 0 1rem 0; text-align:center;'>", unsafe_allow_html=True)
if banner_path.exists():
    st.image(str(banner_path), width=700)
else:
    st.markdown("<h1 style='text-align:center;'>brieflyAI</h1>", unsafe_allow_html=True)
st.markdown("</div>", unsafe_allow_html=True)
st.caption("Upload a meeting transcript (.txt / .vtt) or import from Drive. Get summaries, action items, owners, and due dates. Push to Google Tasks.")
st.divider()

# ===================================================
# Config & constants
# ===================================================
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
TASKS_SCOPES = ["https://www.googleapis.com/auth/tasks"]
RESULT_COLS = ["File", "Summary", "Action", "Owner", "Due"]

# ===================================================
# Utilities
# ===================================================

# Date + VTT helpers
ISO_DATE_RE   = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
VTT_TS_LINE   = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}")
VTT_INLINE_TS = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?(?:\.\d{1,3})?\b")  # 0:12, 01:02:03

def only_future_iso_or_none(s: str | None) -> str:
    """Return YYYY-MM-DD if present AND >= today; else 'None'."""
    if not s:
        return "None"
    m = ISO_DATE_RE.search(s)
    if not m:
        return "None"
    try:
        d = datetime.strptime(m.group(0), "%Y-%m-%d").date()
        return d.isoformat() if d >= date.today() else "None"
    except Exception:
        return "None"

def parse_vtt(vtt_text: str) -> str:
    """Remove cue lines and inline timestamps; keep spoken content."""
    kept = []
    for line in vtt_text.splitlines():
        if VTT_TS_LINE.match(line):
            continue
        line = VTT_INLINE_TS.sub(" ", line)
        line = line.strip()
        if not line:
            continue
        if line.isdigit():  # cue index
            continue
        if line.lower().startswith(("webvtt", "kind:", "language:")):
            continue
        kept.append(line)
    return re.sub(r"\s{2,}", " ", " ".join(kept)).strip()

def read_uploaded_text(file) -> str:
    content = file.read()
    try:
        txt = content.decode("utf-8", errors="ignore")
    except Exception:
        txt = str(content)
    if file.name.lower().endswith(".vtt"):
        return parse_vtt(txt)
    return txt

def extract_json(text: str) -> t.Optional[dict]:
    """Robustly extract the last valid JSON object/array from an LLM response."""
    # ```json ... ```
    m = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # any {...} or [...]
    candidates = re.findall(r"(\{.*\}|\[.*\])", text, re.S)
    for cand in reversed(candidates):
        try:
            return json.loads(cand)
        except Exception:
            continue
    # full string
    try:
        return json.loads(text)
    except Exception:
        return None

# ===================================================
# LLM setup & prompt
# ===================================================
@st.cache_resource
def get_llm():
    # temperature 0 for structured consistency
    return OllamaLLM(model="llama3", temperature=0)

LLM = get_llm()

OUTPUT_SCHEMA = """
Return ONLY valid JSON (no backticks, no prose), exactly:

{
  "summary": "2–3 sentence summary of the meeting; plain text.",
  "items": [
    {"action": "what needs to be done", "owner": "person or 'None'", "due": "YYYY-MM-DD or 'None'"}
  ]
}
"""

def build_prompt(transcript: str) -> str:
    return (
        "You are a meticulous meeting parser.\n\n"
        "RULES:\n"
        "1) The input may come from a WebVTT file. IGNORE all timestamp lines (e.g., '00:00:01.000 --> 00:00:04.000') "
        "   and any inline time tokens like '0:12' or '01:02:03' — these are NOT dates.\n"
        "2) Extract only concrete decisions and action items. No chit-chat.\n"
        "3) If an owner/due date is not explicitly clear, set it to 'None'. Do NOT guess.\n"
        "4) Dates MUST be ISO (YYYY-MM-DD). If you see natural language dates, convert them; otherwise 'None'.\n"
        "5) Output STRICT JSON per the schema below. No explanations.\n\n"
        f"{OUTPUT_SCHEMA}\n\n"
        f"Transcript (timestamps already removed where possible):\n{transcript}\n"
    )

def analyze_transcript(transcript: str) -> dict:
    prompt = build_prompt(transcript)
    raw = LLM.invoke(prompt)
    data = extract_json(raw)
    if not data:
        data = {"summary": raw[:1000], "items": []}
    # normalize items
    clean_items = []
    for it in data.get("items", []):
        action = (it.get("action") or "").strip()
        owner  = (it.get("owner")  or "None").strip() or "None"
        due    = only_future_iso_or_none(it.get("due"))
        clean_items.append({"action": action or "None", "owner": owner, "due": due})
    data["items"] = clean_items
    return data

# ===================================================
# Google OAuth helpers (Drive & Tasks)
# ===================================================
def ensure_creds(token_path: str, scopes: list[str]) -> t.Optional[Credentials]:
    """Try token.json, else run local OAuth using credentials.json. Returns Credentials or None."""
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, scopes)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        if not creds:
            if not os.path.exists("credentials.json"):
                st.error("Missing credentials.json for Google OAuth.")
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", scopes)
                creds = flow.run_local_server(port=0)
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
            except Exception as e:
                st.error(f"OAuth failed: {e}")
                return None
    return creds

def list_drive_transcripts(service) -> list[dict]:
    """Find .txt or .vtt in Drive (non-trashed). Returns [{id, name}, ...]."""
    q = "trashed=false and (mimeType='text/plain' or name contains '.vtt')"
    results = service.files().list(q=q, pageSize=50, fields="files(id,name,mimeType,modifiedTime)").execute()
    return results.get("files", [])

def download_drive_file(service, file_id: str) -> str:
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(fd=buf, request=request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    buf.seek(0)
    raw = buf.read().decode("utf-8", errors="ignore")
    return parse_vtt(raw) if ("WEBVTT" in raw.upper()) else raw

def push_to_google_tasks(creds: Credentials, title: str, notes: str, due_iso: str) -> str:
    tasks_service = build("tasks", "v1", credentials=creds)
    lists = tasks_service.tasklists().list().execute().get("items", [])
    list_id = lists[0]["id"] if lists else "@default"
    body = {"title": title}
    if notes:
        body["notes"] = notes
    if due_iso != "None":
        body["due"] = f"{due_iso}T09:00:00.000Z"  # RFC3339
    created = tasks_service.tasks().insert(tasklist=list_id, body=body).execute()
    return created.get("id", "")

# ===================================================
# Session state
# ===================================================
if "rows" not in st.session_state:
    st.session_state.rows = []  # list[dict] with RESULT_COLS

# ===================================================
# Import from Google Drive (optional)
# ===================================================
with st.expander("Import from Google Drive (optional)"):
    if st.button("Connect Google Drive"):
        creds = ensure_creds("token.drive.json", DRIVE_SCOPES)
        if creds:
            st.session_state.drive_creds = creds
            st.success("Connected to Google Drive.")

    drive_files: list[dict] = []
    if "drive_creds" in st.session_state:
        try:
            drive_service = build("drive", "v3", credentials=st.session_state.drive_creds)
            drive_files = list_drive_transcripts(drive_service)
            names = [f["name"] for f in drive_files]
            select = st.multiselect("Choose transcripts from Drive", names)
            if st.button("Summarize Selected from Drive"):
                with st.spinner("Summarizing from Drive..."):
                    for name in select:
                        meta = next((f for f in drive_files if f["name"] == name), None)
                        if not meta:
                            continue
                        text = download_drive_file(drive_service, meta["id"])
                        data = analyze_transcript(text)
                        if data.get("items"):
                            for it in data["items"]:
                                st.session_state.rows.append({
                                    "File": name,
                                    "Summary": data.get("summary", ""),
                                    "Action": it.get("action", ""),
                                    "Owner": it.get("owner", "None"),
                                    "Due": it.get("due", "None"),
                                })
                        else:
                            st.session_state.rows.append({
                                "File": name,
                                "Summary": data.get("summary", ""),
                                "Action": "",
                                "Owner": "None",
                                "Due": "None",
                            })
                st.success("Done.")
        except Exception as e:
            st.error(f"Drive error: {e}")

st.divider()

# ===================================================
# Upload & analyze transcripts
# ===================================================
st.subheader("Upload Transcript")
meeting_title = st.text_input("Meeting title", value="Weekly Sync")
uploaded = st.file_uploader("Drop .txt or .vtt transcript(s)", type=["txt", "vtt"], accept_multiple_files=True)

if uploaded and st.button("Analyze Uploaded"):
    with st.spinner("Analyzing..."):
        for up in uploaded:
            text = read_uploaded_text(up)
            data = analyze_transcript(text)
            if data.get("items"):
                for it in data["items"]:
                    st.session_state.rows.append({
                        "File": up.name,
                        "Summary": data.get("summary", ""),
                        "Action": it.get("action", ""),
                        "Owner": it.get("owner", "None"),
                        "Due": it.get("due", "None"),
                    })
            else:
                st.session_state.rows.append({
                    "File": up.name,
                    "Summary": data.get("summary", ""),
                    "Action": "",
                    "Owner": "None",
                    "Due": "None",
                })
    st.success("Parsed.")

st.divider()

# ===================================================
# Results table + CSV
# ===================================================
st.subheader("Results")
df = pd.DataFrame(st.session_state.rows, columns=RESULT_COLS) if st.session_state.rows else pd.DataFrame(columns=RESULT_COLS)
st.dataframe(df, use_container_width=True, height=420)

csv = df.to_csv(index=False).encode("utf-8")
st.download_button("Download CSV", data=csv, file_name=f"{meeting_title.replace(' ','_')}_actions.csv", mime="text/csv")

st.divider()

# ===================================================
# Push to Google Tasks
# ===================================================
st.subheader("Push Action Items to Google Tasks")

to_push = st.multiselect(
    "Choose which rows to push",
    options=[f"{i}: {r['Action']}" for i, r in df.iterrows()],
    default=[f"{i}: {r['Action']}" for i, r in df.iterrows() if r["Action"]],
)

if st.button("➕ Create Google Tasks for Selected"):
    if not to_push:
        st.warning("Select at least one row with an Action.")
    else:
        creds = ensure_creds("token.tasks.json", TASKS_SCOPES)
        if creds is None:
            st.stop()
        created = 0
        for opt in to_push:
            idx = int(opt.split(":")[0])
            row = df.iloc[idx]
            if not row["Action"]:
                continue
            title = row["Action"][:255]
            notes = f"{meeting_title}\nOwner: {row['Owner']}\nFile: {row['File']}\n\nSummary:\n{row['Summary']}"
            due = row["Due"] if row["Due"] else "None"
            try:
                push_to_google_tasks(creds, title, notes, due)
                created += 1
            except Exception as e:
                st.error(f"Task create error for row {idx}: {e}")
        st.success(f"Created {created} task(s).")
