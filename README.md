# BrieflyAI — Your Local AI Assistant for Meeting Summaries

>  From meeting chaos to clarity in just a few seconds.

**BrieflyAI** is a lightweight AI-powered assistant that helps you extract clean, actionable summaries and task lists from your meeting transcripts. Built with privacy, speed, and flexibility in mind, it runs **locally** using **open-source LLMs** and integrates directly with **Google Drive** to fetch your meeting recordings or transcripts.

---

## Why I Built Briefly

I was tired of letting good ideas get lost in messy notes and long transcripts. BrieflyAI started as a personal tool to help me turn unstructured meeting chaos into clarity, but quickly grew into something I wanted others to benefit from too.

---

## How It Works

1. **Fetches your Google Meet transcript** directly from Google Drive  
2. **Parses and cleans** the content (removes filler, system prompts, etc.)  
3. **Feeds it to an LLM** (LLaMA 3 via Ollama)  
4. **Generates**:
   - A clear summary of key discussion points  
   - A list of action items / decisions made  
   - Outputs in Markdown and/or JSON formats (ready for Notion, Asana, etc.)

---

## Tech Stack

| Component         | Purpose                                  |
|------------------|------------------------------------------|
| `Python`         | Core logic, file parsing, orchestration  |
| `Google Drive API` | Fetch transcripts from Drive             |
| `Ollama` + `LLaMA 3` | Local LLM for summarization and extraction |
| `Streamlit`      | Lightweight UI for quick testing and tweaks |
| `Docker`         | Containerization for portability and easy setup |

> **Local-first by design**: No external calls to hosted LLMs — ensuring your meetings remain private and secure.

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/chankarnam/briefly-ai.git
cd briefly-ai
```

### 📦 2. Set Up Environment

Install dependencies:

```bash
pip install -r requirements.txt
```

Make sure Ollama is installed and running:

``` bash
ollama run llama3
```

Set up your .env file with Google API credentials:

```ini
GOOGLE_CLIENT_ID=xxx
GOOGLE_CLIENT_SECRET=xxx
GOOGLE_REFRESH_TOKEN=xxx
```
(Or follow the OAuth flow built into the app.)

### 3. Run the app

```bash
streamlit run app.py
```

