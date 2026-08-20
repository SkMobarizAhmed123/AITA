# ──────────────────────────────────────────────────────────────────────────────
# code_rag.py — Repository Chat & Code RAG Engine for Varic
#
# Scans and indexes local source code repositories (.py, .js, .ts, .html, .css, etc.),
# performs semantic symbol extraction & code chunking, and provides instant code search.
# ──────────────────────────────────────────────────────────────────────────────
from config import BASE_DIR
import os
import re
import time
import math
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Tuple
import threading

VARIC_DIR = os.environ.get("VARIC_DIR", str(BASE_DIR))
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
    ".json", ".md", ".sql", ".cpp", ".c", ".h", ".java", ".rs", ".go"
}
IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".antigravity-ide", "brain", "loading", "actions"
}
INDEX_CACHE_FILE = os.path.join(VARIC_DIR, "code_rag_cache.pkl")


@dataclass
class CodeChunk:
    filepath: str
    relpath: str
    symbol_name: str
    content: str
    start_line: int
    end_line: int
    tokens: List[str] = field(default_factory=list)
    file_hash: str = ""  # Hash of file content for change detection


def tokenize_code(text: str) -> List[str]:
    """Tokenize code text into camelCase/snake_case words."""
    words = re.findall(r'[a-zA-Z0-9_]+', text)
    tokens = []
    for w in words:
        # Split camelCase and snake_case
        subwords = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', w).lower().split('_')
        for sw in subwords:
            sw_clean = sw.strip()
            if len(sw_clean) > 1:  # Filter out single characters
                tokens.append(sw_clean)
    return tokens


class CodeRAGEngine:
    def __init__(self, workspace_dir: str = VARIC_DIR):
        self.workspace_dir = workspace_dir
        self.chunks: List[CodeChunk] = []
        self.doc_freqs: Counter = Counter()
        self.total_docs: int = 0
        self.last_indexed_time: float = 0.0
        self.file_states: Dict[str, str] = {}  # filepath -> hash
        self._lock = threading.RLock()  # Reentrant lock for thread safety

        # Try to load cached index
        self._load_cache()

    def _get_file_hash(self, filepath: str) -> str:
        """Compute hash of file content for change detection."""
        try:
            with open(filepath, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def _load_cache(self) -> bool:
        """Load cached index if available and valid."""
        try:
            if os.path.exists(INDEX_CACHE_FILE):
                with open(INDEX_CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)

                # Validate cache
                if (cache_data.get('workspace_dir') == self.workspace_dir and
                    cache_data.get('timestamp') > 0):

                    raw_chunks = cache_data.get('chunks', [])
                    self.chunks = [CodeChunk(**c) for c in raw_chunks]
                    self.doc_freqs = Counter(cache_data.get('doc_freqs', {}))
                    self.total_docs = len(self.chunks)
                    self.file_states = cache_data.get('file_states', {})
                    self.last_indexed_time = cache_data.get('timestamp', 0)

                    print(f"[CODE RAG] Loaded cache with {self.total_docs} chunks")
                    return True
        except Exception as e:
            print(f"[CODE RAG WARNING] Failed to load cache: {e}")
        return False

    def _save_cache(self):
        """Save current index to cache."""
        try:
            cache_data = {
                'workspace_dir': self.workspace_dir,
                'timestamp': time.time(),
                'chunks': self.chunks,
                'doc_freqs': self.doc_freqs,
                'file_states': self.file_states,
            }
            with open(INDEX_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                'workspace_dir': cache_data['workspace_dir'],
                'timestamp': cache_data['timestamp'],
                'chunks': [c.__dict__ for c in cache_data['chunks']],
                'doc_freqs': dict(cache_data['doc_freqs']),
                'file_states': cache_data['file_states']
            }, f)
            print(f"[CODE RAG] Saved cache with {self.total_docs} chunks")
        except Exception as e:
            print(f"[CODE RAG WARNING] Failed to save cache: {e}")

    def index_directory(self, target_dir: Optional[str] = None, force_rebuild: bool = False) -> Dict:
        """Scan and index all source code files in target_dir with incremental updates."""
        root_dir = target_dir or self.workspace_dir
        if not os.path.exists(root_dir):
            return {"status": "error", "message": f"Directory not found: {root_dir}"}

        with self._lock:
            print(f"[CODE RAG] Indexing repository: {root_dir}...")
            start_time = time.time()

            # Track what we find in this scan
            current_files: Set[str] = set()
            new_chunks: List[CodeChunk] = []
            updated_files = 0
            new_files = 0
            deleted_files = 0

            # Walk through directory
            for root, dirs, files in os.walk(root_dir):
                # Modify dirs in-place to skip ignored directories
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext in CODE_EXTENSIONS:
                        filepath = os.path.join(root, file)
                        relpath = os.path.relpath(filepath, root_dir)
                        current_files.add(filepath)

                        # Get current file hash
                        current_hash = self._get_file_hash(filepath)

                        # Check if file is new or modified
                        if filepath not in self.file_states:
                            # New file
                            new_files += 1
                            needs_indexing = True
                        elif self.file_states[filepath] != current_hash:
                            # Modified file
                            updated_files += 1
                            needs_indexing = True
                            # Remove old chunks for this file
                            self.chunks = [c for c in self.chunks if c.filepath != filepath]
                        else:
                            # Unchanged file
                            needs_indexing = False

                        if needs_indexing or force_rebuild:
                            try:
                                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                                    content = f.read()

                                file_chunks = self._chunk_file(filepath, relpath, content)
                                # Update file hash in chunks
                                for chunk in file_chunks:
                                    chunk.file_hash = current_hash

                                new_chunks.extend(file_chunks)
                            except Exception as e:
                                print(f"[CODE RAG NOTE] Could not read {relpath}: {e}")

            # Handle deleted files
            tracked_files = set(self.file_states.keys())
            deleted_files = tracked_files - current_files
            if deleted_files:
                # Remove chunks for deleted files
                self.chunks = [c for c in self.chunks if c.filepath not in deleted_files]
                # Update file_states
                for filepath in deleted_files:
                    del self.file_states[filepath]

            # Add new/updated chunks
            self.chunks.extend(new_chunks)

            # Update file states
            for filepath in current_files:
                if filepath not in self.file_states:
                    self.file_states[filepath] = self._get_file_hash(filepath)

            # Rebuild document frequencies
            self._rebuild_doc_freqs()

            self.total_docs = len(self.chunks)
            self.last_indexed_time = time.time()

            # Save cache
            self._save_cache()

            elapsed = time.time() - start_time
            print(f"[CODE RAG READY] Indexed {len(current_files)} files "
                  f"(+{new_files} new, +{updated_files} updated, -{deleted_files} deleted) "
                  f"in {elapsed:.2f}s ({self.total_docs} chunks)")

            return {
                "status": "ok",
                "files_indexed": len(current_files),
                "new_files": new_files,
                "updated_files": updated_files,
                "deleted_files": deleted_files,
                "chunks_indexed": self.total_docs,
                "root_dir": root_dir,
                "indexing_time": round(elapsed, 2)
            }

    def _rebuild_doc_freqs(self):
        """Rebuild document frequencies from current chunks."""
        self.doc_freqs = Counter()
        for chunk in self.chunks:
            unique_tokens = set(chunk.tokens)
            for t in unique_tokens:
                self.doc_freqs[t] += 1

    def _chunk_file(self, filepath: str, relpath: str, content: str) -> List[CodeChunk]:
        """Split a file into logical chunks (functions/classes or fixed windows)."""
        lines = content.splitlines()
        if not lines:
            return []

        chunks = []
        ext = os.path.splitext(filepath)[1].lower()

        if ext in (".py", ".js", ".ts", ".jsx", ".tsx"):
            # Split by class / function definitions
            current_symbol = os.path.basename(filepath)
            current_lines = []
            start_line = 1

            for idx, line in enumerate(lines, 1):
                # Detect def / class / function keywords
                m = re.match(r'^(?:async\s+)?(?:def|class|function|const|let|var)\s+([a-zA-Z0-9_]+)', line.strip())
                if m and current_lines:
                    chunk_text = "\n".join(current_lines).strip()
                    if len(chunk_text) > 30:
                        chunks.append(CodeChunk(
                            filepath=filepath,
                            relpath=relpath,
                            symbol_name=current_symbol,
                            content=chunk_text,
                            start_line=start_line,
                            end_line=idx - 1,
                            tokens=tokenize_code(chunk_text)
                        ))
                    current_symbol = m.group(1)
                    current_lines = [line]
                    start_line = idx
                else:
                    current_lines.append(line)

            if current_lines:
                chunk_text = "\n".join(current_lines).strip()
                if len(chunk_text) > 30:
                    chunks.append(CodeChunk(
                        filepath=filepath,
                        relpath=relpath,
                        symbol_name=current_symbol,
                        content=chunk_text,
                        start_line=start_line,
                        end_line=len(lines),
                        tokens=tokenize_code(chunk_text)
                    ))
        else:
            # Fixed line window chunking (50 lines per chunk)
            chunk_size = 50
            for i in range(0, len(lines), chunk_size):
                chunk_lines = lines[i:i + chunk_size]
                chunk_text = "\n".join(chunk_lines).strip()
                if len(chunk_text) > 30:
                    chunks.append(CodeChunk(
                        filepath=filepath,
                        relpath=relpath,
                        symbol_name=os.path.basename(filepath),
                        content=chunk_text,
                        start_line=i + 1,
                        end_line=i + len(chunk_lines),
                        tokens=tokenize_code(chunk_text)
                    ))

        return chunks

    def search_code(self, query: str, limit: int = 4) -> List[Dict]:
        """Search code chunks using TF-IDF ranking with caching."""
        if not self.chunks:
            # Try to load cache if we have no chunks
            if not self._load_cache():
                self.index_directory()  # Initial indexing if no cache

        query_tokens = tokenize_code(query)
        if not query_tokens:
            return []

        scores = []
        for idx, chunk in enumerate(self.chunks):
            score = 0.0
            chunk_token_counts = Counter(chunk.tokens)
            total_chunk_tokens = len(chunk.tokens) or 1

            for qt in query_tokens:
                if qt in chunk_token_counts:
                    tf = chunk_token_counts[qt] / total_chunk_tokens
                    df = self.doc_freqs.get(qt, 1)
                    idf = math.log((self.total_docs + 1) / (df + 1)) + 1.0
                    score += tf * idf

                    # Boost score if symbol_name matches query
                    if qt in chunk.symbol_name.lower():
                        score += 2.0

            if score > 0.0:
                scores.append((score, chunk))

        scores.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, chunk in scores[:limit]:
            results.append({
                "filepath": chunk.filepath,
                "relpath": chunk.relpath,
                "symbol_name": chunk.symbol_name,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "score": round(score, 4),
                "snippet": chunk.content[:1000]
            })

        return results

    def format_rag_context(self, query: str, limit: int = 3) -> str:
        """Format top relevant code snippets into prompt context."""
        results = self.search_code(query, limit=limit)
        if not results:
            return ""

        context_blocks = ["[REPOSITORY CODE CONTEXT]"]
        for r in results:
            context_blocks.append(
                f"--- File: {r['relpath']} (Lines {r['start_line']}-{r['end_line']}) ---\n"
                f"{r['snippet']}\n"
            )
        return "\n".join(context_blocks)


# Global singleton instance
code_rag_engine = CodeRAGEngine()

if __name__ == "__main__":
    res = code_rag_engine.index_directory(str(BASE_DIR))
    print("Index result:", res)
    matches = code_rag_engine.search_code("find free port")
    print("Search matches:", len(matches))
    if matches:
        print("Top match:", matches[0]["relpath"], matches[0]["symbol_name"])
