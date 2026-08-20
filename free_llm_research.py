import os
import json
import time
import requests
from typing import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

try:
    from bs4 import BeautifulSoup
    from duckduckgo_search import DDGS
except ImportError:
    BeautifulSoup = None
    DDGS = None

load_dotenv()

def is_multi_ai_research_command(message: str) -> bool:
    msg = message.lower().strip()
    prefixes = ["/aires", "/checkres", "/combres"]
    return any(msg.startswith(p) for p in prefixes)

def _fetch_wikipedia_api(topic: str) -> tuple[str, dict]:
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AITA/1.0'}
        url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={requests.utils.quote(topic)}&format=json"
        r = requests.get(url, timeout=5, headers=headers).json()
        search_results = r.get("query", {}).get("search", [])
        if not search_results:
            return "", {}
        
        page_id = search_results[0]["pageid"]
        title = search_results[0]["title"]
        
        ext_url = f"https://en.wikipedia.org/w/api.php?action=query&prop=extracts&exintro&explaintext&pageids={page_id}&format=json"
        ext_r = requests.get(ext_url, timeout=5, headers=headers).json()
        pages = ext_r.get("query", {}).get("pages", {})
        extract = pages.get(str(page_id), {}).get("extract", "")
        if extract:
            wiki_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
            return extract[:1500], {"type": "Wikipedia", "title": f"Wikipedia: {title}", "url": wiki_url}
    except Exception:
        pass
    return "", {}

def _fetch_arxiv_api(topic: str) -> tuple[str, list]:
    try:
        url = f"http://export.arxiv.org/api/query?search_query=all:{requests.utils.quote(topic)}&start=0&max_results=3"
        r = requests.get(url, timeout=6)
        if r.status_code == 200 and BeautifulSoup:
            soup = BeautifulSoup(r.content, 'xml')
            entries = soup.find_all('entry')
            text_parts = []
            sources = []
            for entry in entries:
                t = entry.title.text.strip().replace('\n', ' ') if entry.title else ""
                summary = entry.summary.text.strip().replace('\n', ' ') if entry.summary else ""
                id_url = entry.id.text.strip() if entry.id else ""
                if summary:
                    text_parts.append(f"Paper ({t}): {summary[:800]}")
                    sources.append({"type": "Academic Paper", "title": f"ArXiv: {t}", "url": id_url})
            return text_parts, sources
    except Exception:
        pass
    return [], []

def _fetch_trusted_news_and_web(topic: str) -> tuple[str, list]:
    if not (DDGS and BeautifulSoup):
        return "", []
    context_parts = []
    sources = []
    try:
        ddg = DDGS()
        res1 = list(ddg.text(f"{topic} news research", max_results=3))
        res2 = list(ddg.text(f"{topic} Reuters OR AP OR BBC OR The Hindu OR Indian Express", max_results=4))
        
        news_res = []
        seen = set()
        for r in res1 + res2:
            if 'href' in r and r['href'] not in seen:
                seen.add(r['href'])
                news_res.append(r)
                
        urls = [r['href'] for r in news_res][:6]
        titles = {r['href']: r.get('title', r['href']) for r in news_res}

        def fetch_url(url):
            try:
                resp = requests.get(url, timeout=6, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                soup = BeautifulSoup(resp.content, 'html.parser')
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text().strip()) > 30]
                text = " ".join(paragraphs[:8])
                if len(text.split()) < 25:
                    return "", None
                t_name = titles.get(url, url)
                return text[:1200], {"type": "News / Web Article", "title": t_name, "url": url}
            except Exception:
                return "", None

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(fetch_url, u) for u in urls]
            for f in as_completed(futures):
                txt, src = f.result()
                if txt and src:
                    sources.append(src)
                    context_parts.append(f"Source: {src['title']}\nContent: {txt}")
    except Exception:
        pass
    return context_parts, sources

def _fetch_news_context(topic: str) -> tuple[str, list]:
    context_parts = []
    sources = []

    # Run Wikipedia, arXiv, and Web News fetching concurrently in 3 parallel threads
    with ThreadPoolExecutor(max_workers=3) as executor:
        f_wiki = executor.submit(_fetch_wikipedia_api, topic)
        f_arxiv = executor.submit(_fetch_arxiv_api, topic)
        f_news = executor.submit(_fetch_trusted_news_and_web, topic)

        wiki_text, wiki_src = f_wiki.result()
        arxiv_texts, arxiv_srcs = f_arxiv.result()
        news_texts, news_srcs = f_news.result()

    if wiki_text and wiki_src:
        sources.append(wiki_src)
        context_parts.append(f"[Ref {len(sources)}] {wiki_src['title']}:\n{wiki_text}")

    if arxiv_texts and arxiv_srcs:
        for text, s in zip(arxiv_texts, arxiv_srcs):
            sources.append(s)
            context_parts.append(f"[Ref {len(sources)}] {s['title']}:\n{text}")

    if news_texts and news_srcs:
        for text, s in zip(news_texts, news_srcs):
            sources.append(s)
            context_parts.append(f"[Ref {len(sources)}] {s['title']}:\n{text}")

    full_context = "\n\n".join(context_parts)
    return full_context, sources

def run_multi_ai_research_stream(raw_message: str) -> Generator[str, None, None]:
    msg = raw_message.strip()
    msg_lower = msg.lower()

    # Determine command mode
    if msg_lower.startswith("/aires"):
        mode = "aires"
        topic = re_sub(r"^/aires/?\s*", "", msg, flags=1)  # 1 = re.IGNORECASE
    elif msg_lower.startswith("/checkres"):
        mode = "checkres"
        topic = re_sub(r"^/checkres/?\s*", "", msg, flags=1)
    elif msg_lower.startswith("/combres"):
        mode = "combres"
        topic = re_sub(r"^/combres/?\s*", "", msg, flags=1)
    else:
        # Default fallback mode
        mode = "combres"
        topic = msg

    # Parse format flags e.g. --pdf, --docx, --doc, --txt, --md
    import re
    export_fmt = "docx"
    flag_match = re.search(r"--(pdf|docx|doc|txt|md)(?=\s|$)", topic, re.IGNORECASE)
    if flag_match:
        export_fmt = flag_match.group(1).lower()
        topic = (topic[:flag_match.start()] + topic[flag_match.end():]).strip()

    mode_titles = {
        "aires": "🤖 Sam's Multi-AI Research (APIs Only)",
        "checkres": "📰 Mobariz's Fact-Checked Research (News + Wiki + Papers)",
        "combres": "⚡ Combined Composite Research (Multi-AI + Web Context)"
    }
    
    yield json.dumps({"type": "chunk", "content": f"\n🔬 *Research Engine Starting: {mode_titles.get(mode, 'Multi-Research')}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🔍 **Topic:** \"{topic}\" (Export format: {export_fmt.upper()})\n\n"}) + "\n"

    # Context fetching logic based on mode
    context = ""
    sources_list = []
    if mode in ["checkres", "combres"]:
        yield json.dumps({"type": "chunk", "content": "> 🌐 Searching top News articles, Wikipedia, and Research Papers...\n"}) + "\n"
        context, sources_list = _fetch_news_context(topic)
        if context:
            yield json.dumps({"type": "chunk", "content": f"> ✅ Extracted {len(sources_list)} verified sources. Feeding context into research engine...\n\n"}) + "\n"
        else:
            yield json.dumps({"type": "chunk", "content": "> ⚠️ Could not fetch web context. Proceeding with base knowledge...\n\n"}) + "\n"

    # Model selection based on mode
    all_models = {
        "Gemini (2.5 Pro)": lambda t: _query_gemini(t, context),
        "ChatGPT (GPT-4o mini)": lambda t: _query_chatgpt(t, context),
        "DeepSeek (DeepSeek-V3)": lambda t: _query_deepseek(t, context),
        "Grok (Grok 4.5)": lambda t: _query_grok(t, context),
        "Perplexity (Sonar Pro)": lambda t: _query_perplexity(t, context),
        "Claude (Claude 3.5 Sonnet)": lambda t: _query_claude(t, context)
    }

    if mode == "checkres":
        # Checkres uses single primary model (Ollama)
        tasks = {"Fact-Checker AI (Ollama)": lambda t: _query_ollama(t, context)}
        yield json.dumps({"type": "chunk", "content": "> 🤖 Synthesizing single unified paper from web evidence via Local Ollama...\n\n"}) + "\n"
    else:
        # Aires & Combres query all models in parallel
        tasks = all_models
        yield json.dumps({"type": "chunk", "content": f"> 🤖 Querying {len(tasks)} AI models in parallel...\n\n"}) + "\n"

    results = {}
    with ThreadPoolExecutor(max_workers=min(len(tasks), 7)) as executor:
        future_to_name = {executor.submit(func, topic): name for name, func in tasks.items()}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result = future.result(timeout=180)
                results[name] = result
                if "Failed" in result["status"]:
                    yield json.dumps({"type": "chunk", "content": f"> 🔴 **{name}** failed: {result['response']}\n"}) + "\n"
                else:
                    yield json.dumps({"type": "chunk", "content": f"> 🟡 **{name}** responded ({result['word_count']} words, {result['time']}s)\n"}) + "\n"
            except Exception as e:
                yield json.dumps({"type": "chunk", "content": f"> 🔴 **{name}** failed: {e}\n"}) + "\n"
                results[name] = {"response": str(e), "word_count": 0}

    if mode == "checkres":
        synthesis = results.get("Fact-Checker AI (Ollama)", {}).get("response", "Ollama failed.")
    else:
        yield json.dumps({"type": "chunk", "content": f"\n🧠  *Synthesizing research paper...*\n\n"}) + "\n"
        synthesis = _synthesize(topic, results, context, mode)
    
    if mode == "checkres":
        final_output = f"# Fact-Checked Research Report: {topic}\n\n{synthesis}\n\n"
    else:
        final_output = f"# Master Research Report: {topic}\n\n## Synthesis & Report\n{synthesis}\n\n"
        final_output += "## Individual AI Model Contributions\n"
        for name, res in results.items():
            wc = res.get('word_count', 0)
            if wc > 20:
                final_output += f"### {name} Insights\n{res['response']}\n\n"

    # Append clean References & Bibliography section
    if sources_list:
        final_output += "## References & Bibliography\n"
        for idx, src in enumerate(sources_list, 1):
            final_output += f"{idx}. **[{src['type']}]** [{src['title']}]({src['url']})\n"
        final_output += "\n"
            
    # Automatically save report to Desktop for ALL research modes ONLY if synthesis succeeded
    if not synthesis.startswith("❌") and "timed out" not in synthesis and "connection error" not in synthesis and "Ollama failed" not in synthesis:
        try:
            from utils.file_utils import save_response_to_file
            save_msg = save_response_to_file(final_output, topic, export_fmt, prefix=mode.upper())
            final_output += f"\n\n{save_msg}"
        except Exception as e:
            final_output += f"\n\n❌ Failed to save file: {e}"
    else:
        final_output += "\n\n⚠️ Document was not saved because synthesis failed."

    yield json.dumps({"type": "chunk", "content": final_output}) + "\n"
    yield json.dumps({"type": "done", "sources": list(tasks.keys())}) + "\n"

def _query_ollama(topic: str, context: str = "") -> dict:
    start = time.time()
    short_context = context[:3500] if context else ""
    
    if short_context:
        prompt = (
            f"You are an expert research scientist and academic writer. Write an authoritative, rigorous, and exhaustive research report on the topic: '{topic}'.\n\n"
            f"Base your report strictly on the provided research context and synthesize core insights:\n"
            f"--- RESEARCH EVIDENCE CONTEXT ---\n{short_context}\n--------------------------------\n\n"
            f"Format your response strictly using the following Markdown structure:\n"
            f"# {topic}\n\n"
            f"## Executive Summary\n\n"
            f"## Key Academic & Empirical Findings\n\n"
            f"## Recent Developments & Industry Context\n\n"
            f"## Critical Discussion & Future Scope\n\n"
            f"## Conclusion"
        )
    else:
        prompt = (
            f"You are an expert research scientist and academic writer. Write an authoritative, rigorous, and exhaustive research report on: '{topic}'.\n\n"
            f"Format your response strictly using the following Markdown structure:\n"
            f"# {topic}\n\n"
            f"## Executive Summary\n\n"
            f"## Key Academic & Empirical Findings\n\n"
            f"## Critical Discussion\n\n"
            f"## Conclusion"
        )
        
    try:
        r = requests.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                "messages": [
                    {"role": "system", "content": "You write structured, objective, and detailed academic research reports in clean Markdown."},
                    {"role": "user", "content": prompt}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_ctx": 8192,
                    "num_predict": 1500
                }
            },
            timeout=300
        )
        r.raise_for_status()
        text = r.json()["message"]["content"]
        elapsed = time.time() - start
        wc = len(text.split())
        return {"status": f"✅ {wc} words ({elapsed:.1f}s)", "response": text, "word_count": wc, "time": elapsed}
    except Exception as e:
        or_key = os.getenv("OPENROUTER_API_KEY")
        if or_key:
            res = _query_openai_compatible("https://openrouter.ai/api/v1/chat/completions", or_key, "google/gemini-2.5-flash-lite", topic, short_context)
            if not res["status"].startswith("❌"):
                res["status"] += " (OpenRouter Fallback)"
                return res
        return {"status": "❌ Failed", "response": f"Ollama error: {e}", "word_count": 0, "time": 0}

def re_sub(pattern, repl, string, flags=0):
    import re
    return re.sub(pattern, repl, string, flags=flags)

def _query_openai_compatible(url: str, key: str, model: str, topic: str, context: str = "") -> dict:
    if not key: 
        return {"status": "❌ Failed (No Key)", "response": "Missing API Key in .env", "word_count": 0, "time": 0}
    start = time.time()
    
    if context:
        prompt = f"Write a comprehensive 1000-word research paper on '{topic}'. Base your paper on the following news, Wikipedia, and research paper context, citing sources:\n\n{context}"
    else:
        prompt = f"Write a comprehensive 1000-word research paper on '{topic}'."
        
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            timeout=90
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        elapsed = time.time() - start
        wc = len(text.split())
        return {"status": f"✅ {wc} words ({elapsed:.1f}s)", "response": text, "word_count": wc, "time": elapsed}
    except Exception as e:
        err_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            err_msg += f" - {e.response.text}"
        return {"status": "❌ Failed", "response": err_msg, "word_count": 0, "time": 0}


def _query_gemini(topic: str, context: str = "") -> dict:
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        res = _query_openai_compatible("https://openrouter.ai/api/v1/chat/completions", or_key, "google/gemini-2.5-flash-lite", topic, context)
        if not res["status"].startswith("❌"):
            return res
    key = os.getenv("GEMINI_API_KEY")
    return _query_openai_compatible("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", key, "gemini-2.5-pro", topic, context)

def _query_chatgpt(topic: str, context: str = "") -> dict:
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        res = _query_openai_compatible("https://openrouter.ai/api/v1/chat/completions", or_key, "openai/gpt-4o-mini", topic, context)
        if not res["status"].startswith("❌"):
            return res
    key = os.getenv("OPENAI_API_KEY")
    return _query_openai_compatible("https://api.openai.com/v1/chat/completions", key, "gpt-4o-mini", topic, context)

def _query_deepseek(topic: str, context: str = "") -> dict:
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        res = _query_openai_compatible("https://openrouter.ai/api/v1/chat/completions", or_key, "deepseek/deepseek-chat", topic, context)
        if not res["status"].startswith("❌"):
            return res
    key = os.getenv("DEEPSEEK_API_KEY")
    return _query_openai_compatible("https://api.deepseek.com/chat/completions", key, "deepseek-chat", topic, context)

def _query_grok(topic: str, context: str = "") -> dict:
    or_key = os.getenv("OPENROUTER_API_KEY")
    last_or_res = None
    if or_key:
        # x-ai/grok-2-1212 / x-ai/grok-beta are retired on OpenRouter — use
        # current model IDs, newest first.
        for m in ["x-ai/grok-4.5", "x-ai/grok-4", "x-ai/grok-2-1212"]:
            res = _query_openai_compatible("https://openrouter.ai/api/v1/chat/completions", or_key, m, topic, context)
            if not res["status"].startswith("❌"):
                return res
            last_or_res = res
    key = os.getenv("GROK_API_KEY")
    if not key:
        # No direct key configured — surface the real OpenRouter failure
        # instead of masking it with a misleading "Missing API Key" message.
        if last_or_res:
            return last_or_res
        return {"status": "❌ Failed (No Key)", "response": "Missing OPENROUTER_API_KEY and GROK_API_KEY in .env", "word_count": 0, "time": 0}
    return _query_openai_compatible("https://api.x.ai/v1/chat/completions", key, "grok-4-latest", topic, context)

def _query_perplexity(topic: str, context: str = "") -> dict:
    or_key = os.getenv("OPENROUTER_API_KEY")
    last_or_res = None
    if or_key:
        # perplexity/sonar and perplexity/sonar-reasoning have been renamed
        # on OpenRouter — use current model IDs.
        for m in ["perplexity/sonar-pro", "perplexity/sonar", "perplexity/sonar-reasoning-pro"]:
            res = _query_openai_compatible("https://openrouter.ai/api/v1/chat/completions", or_key, m, topic, context)
            if not res["status"].startswith("❌"):
                return res
            last_or_res = res
    key = os.getenv("PERPLEXITY_API_KEY")
    if not key:
        if last_or_res:
            return last_or_res
        return {"status": "❌ Failed (No Key)", "response": "Missing OPENROUTER_API_KEY and PERPLEXITY_API_KEY in .env", "word_count": 0, "time": 0}
    return _query_openai_compatible("https://api.perplexity.ai/chat/completions", key, "sonar-pro", topic, context)

def _query_claude(topic: str, context: str = "") -> dict:
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        res = _query_openai_compatible("https://openrouter.ai/api/v1/chat/completions", or_key, "anthropic/claude-3-haiku", topic, context)
        if not res["status"].startswith("❌"):
            return res
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return {"status": "❌ Failed (No Key)", "response": "Missing API Key in .env", "word_count": 0, "time": 0}
    start = time.time()
    if context:
        prompt = f"Write a comprehensive 1000-word research paper on '{topic}'. Base your paper on the following news, Wikipedia, and research paper context:\n\n{context}"
    else:
        prompt = f"Write a comprehensive 1000-word research paper on '{topic}'."
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 3000,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=90
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
        elapsed = time.time() - start
        wc = len(text.split())
        return {"status": f"✅ {wc} words ({elapsed:.1f}s)", "response": text, "word_count": wc, "time": elapsed}
    except Exception as e:
        err_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            err_msg += f" - {e.response.text}"
        return {"status": "❌ Failed", "response": err_msg, "word_count": 0, "time": 0}

def _synthesize(topic: str, results: dict, context: str = "", mode: str = "") -> str:
    successful_reports = [f"=== {name} ===\n{res['response'][:3000]}" for name, res in results.items() if res.get("word_count", 0) > 20]
    if not successful_reports:
        return "Synthesis failed: No models returned successful results."
    
    combined = "\n\n".join(successful_reports)
    
    sys_prompt = "You are a master research analyst and academic writer."
    user_prompt = f"Synthesize these findings on '{topic}':\n\n{combined}\n\n"
    if context:
        user_prompt += f"Original Context from News/Wiki/Papers:\n{context[:3000]}\n\n"
    
    word_count = "1500" if mode == "aires" else "1000"
    user_prompt += f"Task: Write a comprehensive, authoritative {word_count}-word master research paper identifying consensus and unique insights. Structure with Executive Summary, Detailed Analysis, Key Findings, and Conclusion."

    if mode == "aires":
        try:
            r = requests.post(
                "http://127.0.0.1:11434/api/chat",
                json={
                    "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_ctx": 8192,
                        "num_predict": 2000
                    }
                },
                timeout=1800
            )
            if r.ok:
                return r.json()["message"]["content"]
            else:
                return f"Synthesis failed (Ollama): HTTP {r.status_code} - {r.text}"
        except Exception as e:
            return f"Synthesis failed (Ollama error): {str(e)}"
    
    errors = []
    for provider, model, url, key_env in [
        ("OpenRouter", "google/gemini-2.5-flash-lite", "https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
        ("OpenRouter-Fallback", "openai/gpt-4o-mini", "https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
        ("Gemini", "gemini-2.5-pro", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY")
    ]:
        key = os.getenv(key_env)
        if key:
            try:
                r = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt}
                    ]},
                    timeout=90
                )
                if r.ok:
                    return r.json()["choices"][0]["message"]["content"]
                else:
                    errors.append(f"{provider} ({model}) HTTP {r.status_code}: {r.text}")
            except Exception as e:
                errors.append(f"{provider} ({model}) Error: {str(e)}")
                
    error_str = "\n".join(errors)
    return f"Synthesis failed: All APIs failed or no API keys available.\nDetails:\n{error_str}"

def process_multi_ai_research_sync(message: str) -> str:
    result = ''
    for chunk in run_multi_ai_research_stream(message):
        try:
            data = json.loads(chunk)
            if data['type'] == 'chunk':
                result += data['content']
        except Exception:
            pass
    return result
