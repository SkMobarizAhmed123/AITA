import os
import re
import requests
from typing import Optional
from utils.file_utils import save_response_to_file

def _query_openrouter_generic(model_id: str, query: str) -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return "❌ Missing OPENROUTER_API_KEY in .env"
        
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model_id, "messages": [{"role": "user", "content": query}]},
            timeout=120
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        err_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            err_msg += f" - {e.response.text}"
        return f"❌ API failed: {err_msg}"

def parse_llm_command(message: str) -> Optional[str]:
    msg_lower = message.lower().strip()
    
    # Parse format flags e.g. --pdf, --docx, --txt, --md
    export_fmt = "docx"
    flag_match = re.search(r"--(pdf|docx|txt|md)\b", msg_lower)
    if flag_match:
        export_fmt = flag_match.group(1).lower()
        message = re.sub(r"--(pdf|docx|txt|md)\b", "", message, flags=re.IGNORECASE).strip()
        msg_lower = message.lower().strip()
    
    models = {
        'chatgpt': 'openai/gpt-4o-mini',
        'chat gpt': 'openai/gpt-4o-mini',
        'gemini': 'google/gemini-2.5-flash-lite',
        'claude': 'anthropic/claude-3-haiku',
        'deepseek': 'deepseek/deepseek-chat',
        'grok': 'x-ai/grok-build-0.1',
        'perplexity': 'perplexity/sonar'
    }
    
    target_name = None
    target_model_id = None
    
    for key, model_id in models.items():
        if key in msg_lower:
            target_name = key.replace(' ', '')
            target_model_id = model_id
            break
            
    if not target_name:
        return None
        
    # Remove trigger words
    query = re.sub(r'\b(ask|search|from|using|on|chatgpt|chat gpt|gemini|claude|deepseek|grok|perplexity)\b', '', message, flags=re.IGNORECASE).strip()
    
    if not query:
        return f"❌ Couldn't find what to ask {target_name}."
        
    response_text = _query_openrouter_generic(target_model_id, query)
    
    if response_text.startswith("❌"):
        return response_text
        
    safe_query = re.sub(r'[^a-zA-Z0-9_\-]', '_', query[:20])
    return save_response_to_file(response_text, query, export_fmt, prefix=f'{target_name.capitalize()}_{safe_query}')
