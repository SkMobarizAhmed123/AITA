# ──────────────────────────────────────────────────────────────────────────────
# main.py — Fast API Backend & Intent Routing Server for AITA / Varic Desktop
# ──────────────────────────────────────────────────────────────────────────────
from utils.logger import get_logger
logger = get_logger(__name__)
import os
import sys
import re
import json
import time
import shutil
import stat
import threading
import tempfile
import subprocess
import webbrowser
from typing import Optional, Generator, List, Dict, Any

import requests
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="AITA Desktop API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
FILES_DIR = os.environ.get("FILES_DIR", os.path.join(BASE_DIR, "files"))
QUARANTINE_DIR = os.path.join(FILES_DIR, "cyber", "quarantine")

# ──────────────────────────────────────────────────────────────────────────────
# Static File & Frontend Routes (Instant Loading & Complete Assets)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def serve_root():
    index_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>AITA Desktop API Running</h1>"


actions_dir = os.path.join(BASE_DIR, "actions")
if os.path.exists(actions_dir):
    app.mount("/actions", StaticFiles(directory=actions_dir), name="actions")


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ──────────────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    sources: Optional[List[str]] = None


def _ndjson(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


# ──────────────────────────────────────────────────────────────────────────────
# Ollama / Local LLM Integration
# ──────────────────────────────────────────────────────────────────────────────
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_AVAILABLE = False
ollama_history: List[Dict[str, str]] = []


omniroute_process = None

@app.on_event("startup")
def startup_event():
    global omniroute_process
    initialize_ollama()
    # Start OmniRoute in the background
    try:
        omniroute_process = subprocess.Popen(
            ["cmd.exe", "/c", "omniroute serve --port 20128 --no-open"],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=os.environ.copy()
        )
        logger.info("🚀 OmniRoute background server started.")
    except Exception as e:
        logger.info(f"⚠️ Failed to start OmniRoute: {e}")

@app.on_event("shutdown")
def shutdown_event():
    global omniroute_process
    if omniroute_process:
        logger.info("🛑 Shutting down OmniRoute background server...")
        # Since shell=True on Windows, we need to kill the process tree to stop node
        subprocess.call(['taskkill', '/F', '/T', '/PID', str(omniroute_process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)



def initialize_ollama() -> bool:
    global OLLAMA_AVAILABLE, OLLAMA_MODEL
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=2)
        if r.status_code == 200:
            models = r.json().get("models", [])
            if models:
                installed_names = [m.get("name") for m in models if m.get("name")]
                if OLLAMA_MODEL not in installed_names and len(installed_names) > 0:
                    OLLAMA_MODEL = installed_names[0]
                OLLAMA_AVAILABLE = True
                return True
    except Exception:
        pass
    OLLAMA_AVAILABLE = False
    return False


def query_ollama(prompt: str) -> str:
    global ollama_history
    if not OLLAMA_AVAILABLE:
        if not initialize_ollama():
            return "❌ Ollama service is unavailable. Make sure Ollama or local LLM server is running."
    try:
        ollama_history.append({"role": "user", "content": prompt})
        payload = {
            "model": OLLAMA_MODEL,
            "messages": ollama_history[-10:],
            "stream": False,
        }
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=30)
        if r.status_code == 200:
            ans = r.json().get("message", {}).get("content", "")
            ollama_history.append({"role": "assistant", "content": ans})
            return ans
        return f"❌ Ollama returned status {r.status_code}"
    except Exception as e:
        return f"❌ Ollama request failed: {e}"


def query_ollama_stream(prompt: str) -> Generator[str, None, None]:
    global ollama_history
    if not OLLAMA_AVAILABLE:
        if not initialize_ollama():
            yield _ndjson({"type": "chunk", "content": "❌ Ollama service is unavailable. Make sure Ollama is running."})
            yield _ndjson({"type": "done", "sources": []})
            return
    try:
        ollama_history.append({"role": "user", "content": prompt})
        payload = {
            "model": OLLAMA_MODEL,
            "messages": ollama_history[-10:],
            "stream": True,
        }
        with requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, stream=True, timeout=30) as r:
            full_text = ""
            for line in r.iter_lines():
                if line:
                    data = json.loads(line.decode("utf-8"))
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        full_text += chunk
                        yield _ndjson({"type": "chunk", "content": chunk})
            ollama_history.append({"role": "assistant", "content": full_text})
            yield _ndjson({"type": "done", "sources": [f"Ollama ({OLLAMA_MODEL})"]})
    except Exception as e:
        yield _ndjson({"type": "chunk", "content": f"❌ Stream error: {e}"})
        yield _ndjson({"type": "done", "sources": []})


# ──────────────────────────────────────────────────────────────────────────────
# Fast System Command Helpers
# ──────────────────────────────────────────────────────────────────────────────
def parse_youtube_command(message: str) -> Optional[str]:
    m = re.search(r'(?:open|launch|play)?\s*(.+?)\s+on\s+youtube', message, re.IGNORECASE)
    if m:
        query = m.group(1).strip()
        url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"
        webbrowser.open(url)
        return f"▶️ Opening '{query}' on YouTube..."
    return None


def search_youtube_command(message: str) -> Optional[str]:
    return parse_youtube_command(message)


def parse_google_search_command(message: str) -> Optional[str]:
    m = re.search(r'(?:google|search)\s+(?:for\s+)?(.+)', message, re.IGNORECASE)
    if m:
        query = m.group(1).strip()
        url = f"https://www.google.com/search?q={requests.utils.quote(query)}"
        webbrowser.open(url)
        return f"🔍 Searching Google for '{query}'..."
    return None


def parse_maps_command(message: str) -> Optional[str]:
    m = re.search(r'(?:map|maps|route|navigate)\s+(?:to\s+)?(.+)', message, re.IGNORECASE)
    if m:
        query = m.group(1).strip()
        url = f"https://www.google.com/maps/search/{requests.utils.quote(query)}"
        webbrowser.open(url)
        return f"🗺️ Opening Google Maps for '{query}'..."
    return None


def share_on_whatsapp(message: str) -> Optional[str]:
    return "📱 WhatsApp file sharing trigger executed."


def parse_whatsapp_message(message: str) -> Optional[str]:
    m = re.search(r'send\s+(?:message\s+)?to\s+([^\s:]+)\s*:\s*(.+)', message, re.IGNORECASE)
    if m:
        contact, text = m.group(1), m.group(2)
        phone = resolve_phone(contact)
        if phone:
            url = f"https://web.whatsapp.com/send?phone={phone}&text={requests.utils.quote(text)}"
            webbrowser.open(url)
            return f"💬 Opening WhatsApp chat with {contact} ({phone})..."
        return f"❌ Contact '{contact}' not found."
    return None


def parse_brightness_command(message: str) -> Optional[str]:
    return "☀️ Adjusting display brightness..."


def parse_volume_command(message: str) -> Optional[str]:
    return "🔊 Adjusting system audio volume..."


def parse_wifi_command(message: str) -> Optional[str]:
    webbrowser.open("ms-settings:network-wifi")
    return "📶 Opening Wi-Fi Settings..."


def parse_bluetooth_command(message: str) -> Optional[str]:
    webbrowser.open("ms-settings:bluetooth")
    return "📡 Opening Bluetooth Settings..."


def parse_control_panel_command(message: str) -> Optional[str]:
    m = re.search(r'(?:open|launch|start)\s+(.+)', message, re.IGNORECASE)
    if m:
        target = m.group(1).strip().lower()
        if "setting" in target:
            webbrowser.open("ms-settings:")
            return "⚙️ Opening Windows Settings..."
        if "control" in target:
            subprocess.Popen(["control"])
            return "⚙️ Opening Control Panel..."
    return None


# ──────────────────────────────────────────────────────────────────────────────
# WhatsApp Background Crawler & Contacts Engine
# ──────────────────────────────────────────────────────────────────────────────
_crawl_lock = threading.Lock()
crawl_status = {"running": False, "error": None, "done": False, "contacts_found": 0}


def crawl_in_background():
    global crawl_status
    with _crawl_lock:
        crawl_status = {"running": True, "error": None, "done": False, "contacts_found": 0}
    try:
        from wa_crawler import crawl_whatsapp_contacts
        res = crawl_whatsapp_contacts()
        with _crawl_lock:
            crawl_status = {"running": False, "error": None, "done": True, "contacts_found": len(res)}
    except Exception as e:
        with _crawl_lock:
            crawl_status = {"running": False, "error": str(e), "done": False, "contacts_found": 0}


def start_crawl_if_idle() -> bool:
    with _crawl_lock:
        if crawl_status["running"]:
            return False
    threading.Thread(target=crawl_in_background, daemon=True).start()
    return True


def load_contacts() -> dict:
    try:
        import wa_crawler
        return wa_crawler.load_contacts()
    except Exception:
        return {}


def resolve_phone(target: str) -> Optional[str]:
    target = target.strip()
    if re.match(r'^\+?\d{10,15}$', target):
        return '+' + target.lstrip('+')
    contacts = load_contacts()
    tl = target.lower()
    if tl in contacts:
        return contacts[tl]
    for name, num in contacts.items():
        if tl in name or name in tl:
            return num
    return None


def handle_tool_command(message: str) -> Optional[str]:
    msg = message.strip().lower()
    original = message.strip()
    match = re.search(
        r'(?:make|create|new)\s+.*?txt\s+file\s+(?:titled|named|called)?\s*["\']?([a-zA-Z0-9_ \-]+)["\']?',
        msg, re.IGNORECASE
    )
    if not match:
        return None
    filename = match.group(1).strip()
    if not filename.endswith(".txt"):
        filename += ".txt"
    content = ""
    cm = re.search(
        r'(?:saying|with content|containing|that says)\s+["\']?(.+?)["\']?(?:\s+(?:at|in|inside|to)\s+|$)',
        original, re.IGNORECASE | re.DOTALL
    )
    if cm:
        content = cm.group(1).strip()
        lm = re.search(r'\s+(at|in|inside|to)\s+', content)
        if lm:
            content = content[:lm.start()]
    location = FILES_DIR
    lm = re.search(r'(?:at|in|inside|to)\s+["\']?([^\'"]+)["\']?$', original, re.IGNORECASE)
    if lm:
        location = lm.group(1).strip().rstrip('/\\')
    filepath = os.path.abspath(os.path.join(location, os.path.basename(filename)))
    try:
        os.makedirs(location, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        preview = content[:100] + ("..." if len(content) > 100 else "")
        return (f'✅ Created: {filename}\n📁 {filepath}\n📝 {preview}'
                if content else f'✅ Created: {filename}\n📁 {filepath}')
    except Exception as e:
        return f'❌ Failed: {e}'


# ── Optimized Pre-compiled Regex Patterns ──
_YOUTUBE_PATTERN = re.compile(r'youtube', re.IGNORECASE)
_GOOGLE_PATTERN = re.compile(r'google', re.IGNORECASE)
_WHATSAPP_PATTERN = re.compile(r'whatsapp', re.IGNORECASE)
_CRAWL_CONTACTS_PATTERN = re.compile(r'crawl\s+contacts|sync\s+contacts|refresh\s+contacts', re.IGNORECASE)
_CRAWL_STATUS_PATTERN = re.compile(r'crawl\s+status|sync\s+status', re.IGNORECASE)
_LIST_CONTACTS_PATTERN = re.compile(r'list\s+contacts|show\s+contacts', re.IGNORECASE)
_CONTACT_LOOKUP_PATTERN = re.compile(r'(?:find|look\s+up|who\s+is)\s+', re.IGNORECASE)
_CREATE_FILE_PATTERN = re.compile(r'(?:make|create|new)\s+.*?txt\s+file\s+(?:titled|named|called)?\s*["\']?([a-zA-Z0-9_ \-]+)["\']?', re.IGNORECASE)
_BRIGHTNESS_PATTERN = re.compile(r'brightness|bright|dim', re.IGNORECASE)
_VOLUME_PATTERN = re.compile(r'volume|sound|mute|unmute|louder|quieter|silent|audio', re.IGNORECASE)
_WIFI_PATTERN = re.compile(r'wifi|wi-fi|wireless', re.IGNORECASE)
_BLUETOOTH_PATTERN = re.compile(r'bluetooth', re.IGNORECASE)
_CONTROL_PATTERN = re.compile(r'open|launch|start|sleep|restart|shutdown|lock|hibernate|settings|control|show', re.IGNORECASE)


# ──────────────────────────────────────────────────────────────────────────────
# Virus Detection & Deletion Command
# ──────────────────────────────────────────────────────────────────────────────
def _quarantine_file(filepath: str) -> str:
    """Move file to quarantine instead of deleting outright."""
    os.makedirs(QUARANTINE_DIR, exist_ok=True)
    filename  = os.path.basename(filepath)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    dest      = os.path.join(QUARANTINE_DIR, f"{timestamp}_{filename}")
    shutil.move(filepath, dest)
    return dest


def _force_delete(filepath: str) -> bool:
    """Force delete even if read-only."""
    try:
        os.chmod(filepath, stat.S_IWRITE)
        os.remove(filepath)
        return True
    except Exception:
        return False


def parse_virus_scan_command(message: str) -> Optional[str]:
    msg_lower = message.lower().strip()

    # ── Trigger keywords ──
    if not any(kw in msg_lower for kw in [
        "virus", "malware", "scan", "threat", "infected",
        "delete", "quarantine", "suspicious", "dangerous", "malicious"
    ]):
        return None

    # ── Extract file path from quotes or Windows path pattern ──
    file_path = None

    # Try quoted path first
    quoted = re.findall(r'["\']([^"\']+)["\']', message)
    for q in quoted:
        if re.search(r'[a-zA-Z]:[\\\/]', q) or os.path.exists(q):
            file_path = q.strip()
            break

    # Try unquoted Windows path
    if not file_path:
        m = re.search(r'([a-zA-Z]:[\\\/][^\s"\']+)', message)
        if m:
            file_path = m.group(1).strip()

    if not file_path:
        return (
            "❌ No file path found in your message.\n"
            "💡 Example:\n"
            "   scan \"C:\\Users\\USER\\Downloads\\suspicious.exe\" for virus\n"
            "   check if \"C:\\Temp\\file.bat\" is a virus and delete it"
        )

    # ── Normalize path ──
    file_path = file_path.replace('/', os.sep).replace('\\', os.sep)

    if not os.path.exists(file_path):
        return f"❌ File not found: `{file_path}`"

    if os.path.isdir(file_path):
        return f"❌ That's a folder, not a file: `{file_path}`"

    # ── Scan the file ──
    reply_lines = [f"🔍 Scanning: `{file_path}`\n"]

    try:
        from cyber_scanner import cyber_scanner
        result = cyber_scanner.scan_file(file_path)
    except ImportError:
        # Fallback if cyber_scanner not installed — basic heuristics only
        result = None

    if result:
        reply_lines.append(f"📊 Risk Level : **{result.risk_level.upper()}** ({result.risk_score}/100)")
        reply_lines.append(f"🔢 SHA256     : `{result.sha256[:20]}...`")
        reply_lines.append(f"📈 Entropy    : {result.entropy}/8.0")
        if result.yara_matches:
            reply_lines.append(f"🎯 YARA Hits  : {', '.join(result.yara_matches)}")
        if result.suspicious_imports:
            reply_lines.append(f"⚠️  Suspicious APIs: {', '.join(result.suspicious_imports[:5])}")
        if result.findings:
            reply_lines.append("\n📋 Findings:")
            for f in result.findings:
                reply_lines.append(f"  • {f}")
        risk = result.risk_level
    else:
        # Basic heuristics without cyber_scanner
        ext = os.path.splitext(file_path)[1].lower()
        dangerous_exts = {
            ".exe", ".dll", ".bat", ".cmd", ".vbs", ".ps1",
            ".scr", ".pif", ".com", ".msi", ".jar", ".hta"
        }
        risk = "medium" if ext in dangerous_exts else "low"
        reply_lines.append(f"⚠️ cyber_scanner not installed — basic check only")
        reply_lines.append(f"📁 Extension: {ext} → {'potentially dangerous' if ext in dangerous_exts else 'low risk'}")

    reply_lines.append("")

    # ── Action based on risk + user intent ──
    wants_delete     = any(kw in msg_lower for kw in ["delete", "remove", "kill", "destroy", "wipe"])
    wants_quarantine = any(kw in msg_lower for kw in ["quarantine", "isolate", "move"])

    if risk == "clean" and not wants_delete:
        reply_lines.append("✅ File appears **clean**. No action taken.")
        reply_lines.append("💡 If you still want to delete it, say:")
        reply_lines.append(f'   "delete \"{file_path}\""')
        return "\n".join(reply_lines)

    if risk in ("high", "critical") or wants_delete or wants_quarantine:
        # Decide: quarantine vs delete
        if wants_delete and not wants_quarantine:
            # Hard delete
            if _force_delete(file_path):
                reply_lines.append(f"🗑️ **DELETED** — file permanently removed.")
                reply_lines.append(f"   `{file_path}`")
            else:
                reply_lines.append(f"❌ Could not delete file.")
                reply_lines.append("💡 Try running Varic as Administrator.")
        else:
            # Quarantine (safer default)
            try:
                dest = _quarantine_file(file_path)
                reply_lines.append(f"🔒 **QUARANTINED** — file moved to safe location.")
                reply_lines.append(f"   From: `{file_path}`")
                reply_lines.append(f"   To  : `{dest}`")
                reply_lines.append(f"\n💡 To permanently delete it, say:")
                reply_lines.append(f'   "permanently delete \"{dest}\""')
            except Exception as e:
                reply_lines.append(f"❌ Could not quarantine: {e}")

    elif risk == "medium":
        reply_lines.append(f"⚠️ File is **suspicious** but not confirmed malicious.")
        reply_lines.append(f"💡 Options:")
        reply_lines.append(f'   • "quarantine \"{file_path}\""  → move to safe location')
        reply_lines.append(f'   • "delete \"{file_path}\""      → permanently remove')
        reply_lines.append(f'   • "ignore \"{file_path}\""      → do nothing')

    elif risk == "low":
        reply_lines.append(f"🟡 File shows **low risk** indicators.")
        if wants_delete:
            if _force_delete(file_path):
                reply_lines.append(f"🗑️ Deleted as requested.")
            else:
                reply_lines.append(f"❌ Could not delete file.")
        else:
            reply_lines.append(f"No action taken. Say 'delete \"{file_path}\"' to remove it.")

    return "\n".join(reply_lines)


def route_command(message: str) -> Optional[ChatResponse]:
    msg = message.lower()

    # ── Long-Term Memory Commands ──
    if msg.startswith("remember ") or msg.startswith("save memory "):
        from memory_engine import memory_engine
        parts = message.split(" ", 2)
        if len(parts) >= 3:
            key, content = parts[1], parts[2]
            memory_engine.save_memory("user_fact", key, content)
            return ChatResponse(reply=f"🧠 **Memory Saved**: Saved [{key}] = '{content}'")
        elif len(parts) == 2:
            memory_engine.save_memory("user_fact", parts[1], parts[1])
            return ChatResponse(reply=f"🧠 **Memory Saved**: '{parts[1]}'")

    if msg in ("show memories", "list memories", "my memories", "memories"):
        from memory_engine import memory_engine
        mems = memory_engine.get_all_memories()
        if not mems:
            return ChatResponse(reply="🧠 No long-term memories saved yet.")
        lines = [f"• **[{m['category'].upper()}] {m['key']}**: {m['content']}" for m in mems]
        return ChatResponse(reply=f"🧠 **Long-Term Memory ({len(mems)} items)**:\n" + "\n".join(lines))

    if msg.startswith("forget ") or msg.startswith("delete memory "):
        from memory_engine import memory_engine
        target = message.split(" ", 1)[1].strip()
        if memory_engine.delete_memory(target):
            return ChatResponse(reply=f"🗑️ Deleted memory for '{target}'.")
        return ChatResponse(reply=f"❌ Could not find memory key '{target}'.")

    # ── Code RAG Repository Chat Commands ──
    if msg.startswith("index repo") or msg.startswith("index codebase") or msg == "scan codebase":
        import code_rag as code_rag_engine
        parts = message.split(" ", 2)
        target_dir = parts[2].strip() if len(parts) > 2 else FILES_DIR
        res = code_rag_engine.index_directory(target_dir)
        return ChatResponse(reply=f"📚 **Codebase Indexed**: Scanned {res.get('files_indexed', 0)} code files ({res.get('chunks_indexed', 0)} chunks) in `{target_dir}`.")

    if msg.startswith("code search:") or msg.startswith("search code:"):
        import code_rag as code_rag_engine
        q = message.split(":", 1)[1].strip()
        results = code_rag_engine.search_code(q, limit=4)
        if not results:
            return ChatResponse(reply=f"🔍 No matching code symbols found for '{q}'.")
        lines = [f"• **{r['relpath']}** (lines {r['start_line']}-{r['end_line']}): `{r['symbol_name']}`" for r in results]
        return ChatResponse(reply=f"🔍 **Code RAG Results for '{q}'**:\n" + "\n".join(lines), sources=[r['relpath'] for r in results])

    # ── ZIM Offline Documentation Commands ──
    if (msg.startswith("zim search:") or msg.startswith("python docs:") or
        msg.startswith("search docs:") or msg.startswith("search zim:")):
        import zim_reader as zim_engine
        q = message.split(":", 1)[1].strip()
        res = zim_engine.search(q, max_results=4)
        if res["status"] != "ok" or not res["results"]:
            return ChatResponse(reply=f"📖 No ZIM documentation entries found for '{q}'. ({res.get('message', '')})")

        cards = []
        for r in res["results"]:
            cards.append(f"### 📄 {r['title']}\n*Path*: `{r['path']}`\n\n> {r['snippet']}\n")

        reply = f"📖 **ZIM Documentation Results for '{q}'** (from `{res['zim_file']}`):\n\n" + "\n---\n".join(cards)
        return ChatResponse(reply=reply, sources=[res['zim_file']])

    # ── Project Generator Commands ──
    if msg.startswith("create project") or msg.startswith("generate project") or msg.startswith("make project"):
        import project_generator
        spec = message.split("project", 1)[1].strip()
        if spec:
            res = project_generator.generate_project_structure(spec)
            files_str = "\n".join([f"  • `{f}`" for f in res['files_created']])
            reply = f"🚀 **Project Generated**: `{res['project_name']}`\n\n**Location**: `{res['target_path']}`\n\n**Files Created**:\n{files_str}\n\n{res['message']}"
            return ChatResponse(reply=reply, sources=[res['target_path']])

    # 🧠 Multi-AI Deep Research Commands (/research/) 🔬
    from free_llm_research import is_multi_ai_research_command
    if is_multi_ai_research_command(message):
        reply = "Free-LLM Multi-AI Research started. Please wait..."
        return ChatResponse(reply=reply, sources=["Gemini", "ChatGPT", "DeepSeek"])

    # ── External AI services (Lazy Loaded) ──
    if any(m in msg for m in ["claude", "deepseek", "grok", "perplexity", "chatgpt", "chat gpt", "gemini"]):
        from llm_commands import parse_llm_command
        r = parse_llm_command(message)
        if r: return ChatResponse(reply=r)

    # YouTube commands
    if _YOUTUBE_PATTERN.search(msg):
        if any(k in msg for k in ["search", "find", "look up", "lookup", "browse"]):
            r = search_youtube_command(message)
            if r:
                return ChatResponse(reply=r)
        r = parse_youtube_command(message)
        if r:
            return ChatResponse(reply=r)

    # Google search commands
    if _GOOGLE_PATTERN.search(msg) and any(k in msg for k in ["search", "find", "look up", "lookup", "google"]):
        r = parse_google_search_command(message)
        if r:
            return ChatResponse(reply=r)

    # Maps/Navigation commands
    if any(k in msg for k in ["map", "maps", "route", "direction", "navigate"]):
        r = parse_maps_command(message)
        if r:
            return ChatResponse(reply=r)

    # WhatsApp commands
    if _WHATSAPP_PATTERN.search(msg):
        fn = share_on_whatsapp if re.search(r'[a-zA-Z]:[\\\/]', message) else parse_whatsapp_message
        r = fn(message)
        if r:
            return ChatResponse(reply=r)

    # Contact crawling commands
    if _CRAWL_CONTACTS_PATTERN.search(msg):
        start_crawl_if_idle()
        return ChatResponse(reply="⏳ Crawling contacts in background...")

    # Contact crawl status
    if _CRAWL_STATUS_PATTERN.search(msg):
        with _crawl_lock:
            if crawl_status["running"]:
                return ChatResponse(reply="⏳ Still crawling...")
            if crawl_status["error"]:
                return ChatResponse(reply=f"❌ {crawl_status['error']}")
            if crawl_status["done"]:
                return ChatResponse(reply=f"✅ Crawled {crawl_status['contacts_found']} contacts.")
        return ChatResponse(reply="No crawl started.")

    # List contacts
    if _LIST_CONTACTS_PATTERN.search(msg):
        contacts = load_contacts()
        if not contacts:
            return ChatResponse(reply="❌ No contacts. Say 'crawl contacts' first.")
        lines = [f"• {n.title()} → {num}" for n, num in contacts.items()]
        return ChatResponse(reply=f"📋 {len(contacts)} contacts:\n" + "\n".join(lines))

    # Contact lookup
    if _CONTACT_LOOKUP_PATTERN.search(msg):
        fm = re.search(r'(?:find|look up|who is)\s+(.+)', message.lower())
        if fm:
            num = resolve_phone(fm.group(1).strip())
            if num:
                return ChatResponse(reply=f"📞 {num}")

    # File creation commands
    if _CREATE_FILE_PATTERN.search(msg):
        r = handle_tool_command(message)
        if r:
            return ChatResponse(reply=r)

    # Brightness commands
    if _BRIGHTNESS_PATTERN.search(msg):
        r = parse_brightness_command(message)
        if r:
            return ChatResponse(reply=r)

    # Volume commands
    if _VOLUME_PATTERN.search(msg):
        r = parse_volume_command(message)
        if r:
            return ChatResponse(reply=r)

    # WiFi commands
    if _WIFI_PATTERN.search(msg):
        r = parse_wifi_command(message)
        if r:
            return ChatResponse(reply=r)

    # Bluetooth commands
    if _BLUETOOTH_PATTERN.search(msg):
        r = parse_bluetooth_command(message)
        if r:
            return ChatResponse(reply=r)

    # Virus scan & delete
    if any(kw in msg for kw in [
        "virus", "malware", "scan", "threat", "infected",
        "quarantine", "suspicious", "dangerous", "malicious"
    ]) or (any(kw in msg for kw in ["delete", "remove"]) and
           re.search(r'[a-zA-Z]:[\\\/]', message)):
        r = parse_virus_scan_command(message)
        if r:
            return ChatResponse(reply=r)

    # Control panel commands
    if _CONTROL_PATTERN.search(msg):
        r = parse_control_panel_command(message)
        if r:
            return ChatResponse(reply=r)

    return None


def process_command(message: str) -> ChatResponse:
    from memory_engine import memory_engine
    r = route_command(message)
    if r:
        memory_engine.log_tool_call("route_command", {"message": message}, "success", r.reply)
        return r
    reply = query_ollama(message)
    memory_engine.log_tool_call("query_ollama", {"message": message}, "success", reply[:200])
    return ChatResponse(reply=reply, sources=["Qwen 2.5 (3B)"])


def process_command_stream(message: str) -> Generator[str, None, None]:
    from memory_engine import memory_engine
    from free_llm_research import is_multi_ai_research_command, run_multi_ai_research_stream
    if is_multi_ai_research_command(message):
        memory_engine.log_tool_call("multi_ai_research", {"message": message}, "started", "")
        yield from run_multi_ai_research_stream(message)
        return

    r = route_command(message)
    if r:
        memory_engine.log_tool_call("route_command", {"message": message}, "success", r.reply[:200])
        yield _ndjson({"type": "chunk", "content": r.reply})
        yield _ndjson({"type": "done", "sources": r.sources})
        return
    memory_engine.log_tool_call("query_ollama_stream", {"message": message}, "started", "")
    yield from query_ollama_stream(message)


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    return process_command(req.message)


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    return StreamingResponse(process_command_stream(req.message), media_type="text/event-stream")


@app.post("/transcribe")
async def transcribe_audio_file(file: UploadFile = File(...)):
    from voice_utils import get_voice_assistant
    va = get_voice_assistant()
    if not va:
        return {"transcription": "", "ai_response": "❌ Whisper model is not loaded."}
    try:
        filename = file.filename or "recording.webm"
        ext = os.path.splitext(filename)[1] or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        try:
            res = va.model.transcribe(tmp_path, fp16=False)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        transcribed_text = res.get("text", "").strip()
        if not transcribed_text:
            return {"transcription": "", "error": "Could not understand audio."}
        return {
            "transcription": transcribed_text
        }
    except Exception as e:
        return {"transcription": "", "error": f"Transcription error: {e}"}


@app.post("/voice")
def voice(duration: int = 5):
    from voice_utils import get_voice_assistant
    va = get_voice_assistant()
    if not va:
        return {"reply": "❌ Voice unavailable."}
    try:
        text = va.listen(duration)
        if not text:
            return {"reply": "❌ Couldn't hear anything."}
        resp = process_command(text)
        va.speak(resp.reply)
        return {"transcribed": text, "reply": resp.reply, "sources": resp.sources}
    except Exception as e:
        return {"reply": f"❌ Voice error: {e}"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": "Qwen 2.5 (3B via Ollama)",
        "ollama": OLLAMA_AVAILABLE,
        "model": OLLAMA_MODEL,
    }


@app.post("/ollama/reset")
@app.post("/engine/reset")
def engine_reset():
    global ollama_history
    ollama_history.clear()
    return {"status": "ok"}


@app.post("/ollama/status")
@app.post("/engine/status")
def engine_status():
    return {
        "running": OLLAMA_AVAILABLE,
        "model": OLLAMA_MODEL,
        "ollama_host": OLLAMA_HOST,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Code RAG & Project Generator Endpoints
# ──────────────────────────────────────────────────────────────────────────────
class ProjectRequest(BaseModel):
    spec: str
    project_name: Optional[str] = None


class CodeRAGIndexRequest(BaseModel):
    path: Optional[str] = None


class CodeRAGQueryRequest(BaseModel):
    query: str
    limit: Optional[int] = 5


@app.post("/project/generate")
def generate_project_api(req: ProjectRequest):
    import project_generator
    return project_generator.generate_project_structure(req.spec, req.project_name)


@app.post("/code-rag/index")
def code_rag_index_api(req: CodeRAGIndexRequest):
    import code_rag as code_rag_engine
    return code_rag_engine.index_directory(req.path)


@app.post("/code-rag/query")
def code_rag_query_api(req: CodeRAGQueryRequest):
    import code_rag as code_rag_engine
    return {"results": code_rag_engine.search_code(req.query, limit=req.limit or 5)}


class ZIMQueryRequest(BaseModel):
    query: str
    zim_path: Optional[str] = None
    limit: Optional[int] = 5


@app.get("/audit-logs")
def get_audit_logs():
    from memory_engine import memory_engine
    return {"logs": memory_engine.get_tool_audit_logs(limit=50)}


@app.post("/zim/search")
def zim_search_api(req: ZIMQueryRequest):
    import zim_reader as zim_engine
    return zim_engine.search(req.query, zim_path=req.zim_path, max_results=req.limit or 5)


# Static file fallback route (MUST be at bottom so API routes match first)
@app.get("/{filename:path}")
def serve_static_file(filename: str):
    file_path = os.path.abspath(os.path.join(BASE_DIR, filename))
    if not file_path.startswith(BASE_DIR):
        raise HTTPException(status_code=403, detail="Forbidden")
    if os.path.isfile(file_path):
        if file_path.endswith(".mp4"):
            return FileResponse(file_path, media_type="video/mp4")
        if file_path.endswith(".webm"):
            return FileResponse(file_path, media_type="video/webm")
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="File not found")


# ──────────────────────────────────────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    initialize_ollama()
    uvicorn.run(app, host="127.0.0.1", port=8000)