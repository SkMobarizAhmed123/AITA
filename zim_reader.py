# ──────────────────────────────────────────────────────────────────────────────
# zim_reader.py — ZIM Archive Reader Engine for Varic
#
# Reads and indexes OpenZIM archives (.zim) using libzim, providing instant offline
# search and text extraction for documentation (e.g., docs.python.org_en_all_2026-05.zim).
# ──────────────────────────────────────────────────────────────────────────────────────
from config import BASE_DIR
import os
import re
import hashlib
import json
import lxml.html
from typing import List, Dict, Optional, Tuple
from functools import lru_cache

try:
    import libzim
    ZIM_AVAILABLE = True
except ImportError:
    ZIM_AVAILABLE = False

# Cache file for search results
SEARCH_CACHE_FILE = str(BASE_DIR / "zim_search_cache.pkl")


def clean_html(html_str: str) -> str:
    """Strips HTML tags and converts entities to plain text using lxml for efficiency."""
    try:
        # Parse HTML and extract text content
        doc = lxml.html.fromstring(html_str)
        # Remove script and style elements
        for elem in doc.xpath('//script | //style'):
            elem.drop_tree()
        # Get text content
        text = doc.text_content()
        # Clean up whitespace
        return re.sub(r'\s+', ' ', text).strip()
    except Exception:
        # Fallback to regex if lxml fails
        clean = re.sub(r'<script.*?>.*?</script>', '', html_str, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<style.*?>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
        # Remove HTML tags
        clean = re.sub(r'<[^>]+>', ' ', clean)
        # Collapse whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean


class ZIMReaderEngine:
    def __init__(self, default_path: str = r"D:\Downloads\docs.python.org_en_all_2026-05.zim"):
        self.default_path = default_path
        self._archives: Dict[str, object] = {}
        self._index_cache: Dict[str, Dict] = {}  # zim_path -> index data
        self._search_cache: Dict[str, List[Dict]] = {}  # query_hash -> results
        self._load_search_cache()

    def get_archive(self, path: Optional[str] = None):
        """Loads and caches a ZIM Archive instance."""
        target_path = path or self.default_path
        if not ZIM_AVAILABLE:
            return None, "❌ `libzim` package is not installed."
        if not os.path.exists(target_path):
            return None, f"❌ ZIM file not found at `{target_path}`."

        if target_path not in self._archives:
            try:
                self._archives[target_path] = libzim.Archive(target_path)
                # Build or load index for this archive
                self._build_or_load_index(target_path)
            except Exception as e:
                return None, f"❌ Failed to open ZIM archive: {e}"

        return self._archives[target_path], "ok"

    def _get_index_path(self, zim_path: str) -> str:
        """Get the path to the index file for a ZIM archive."""
        # Create a hash of the path for the filename
        path_hash = hashlib.md5(zim_path.encode()).hexdigest()
        return os.path.join(os.path.dirname(zim_path), f"zim_index_{path_hash}.pkl")

    def _build_or_load_index(self, zim_path: str):
        """Build or load search index for a ZIM archive."""
        index_path = self._get_index_path(zim_path)

        # Try to load existing index
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
                # Check if index is still valid (compare modification times)
                if os.path.getmtime(zim_path) <= index_data.get('timestamp', 0):
                    self._index_cache[zim_path] = index_data
                    return
            except Exception:
                pass  # Failed to load index, will rebuild

        # Build new index
        archive, msg = self.get_archive(zim_path)
        if not archive:
            return

        print(f"[ZIM READER] Building search index for {os.path.basename(zim_path)}...")
        start_time = time.time()

        # Build inverted index: term -> list of (entry_id, score, title, path)
        inverted_index = {}
        total_entries = archive.entry_count

        for i in range(total_entries):
            try:
                e = archive._get_entry_by_id(i)
                path = e.path or ""
                path_lower = path.lower()

                # Filter to real HTML documentation pages
                if "analytics" in path_lower or not path_lower.endswith(".html"):
                    continue

                item = e.get_item()
                if item.size < 1000 or item.mimetype != "text/html":
                    continue

                title = e.title or ""
                title_lower = title.lower()

                # Get content for indexing
                try:
                    html_content = bytes(item.content).decode("utf-8", errors="ignore")
                    text_content = clean_html(html_content)

                    # Index title and content
                    terms = set(re.findall(r'\b[a-zA-Z]{3,}\b', (title_lower + " " + text_content).lower()))

                    for term in terms:
                        if term not in inverted_index:
                            inverted_index[term] = []
                        # Simple scoring: title matches get higher weight
                        score = 2.0 if term in title_lower else 1.0
                        inverted_index[term].append((i, score, title or path, path))
                except Exception:
                    continue  # Skip problematic entries

            except Exception:
                continue  # Skip problematic entries

        # Store index data
        index_data = {
            'timestamp': time.time(),
            'inverted_index': inverted_index,
            'entry_count': total_entries,
            'zim_path': zim_path
        }
        self._index_cache[zim_path] = index_data

        # Save to disk
        try:
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(index_data, f)
        except Exception as e:
            print(f"[ZIM WARNING] Failed to save index: {e}")

        print(f"[ZIM READER] Index built in {time.time() - start_time:.2f}s with {len(inverted_index)} terms")

    def search(self, query: str, zim_path: Optional[str] = None, max_results: int = 5) -> Dict:
        """Searches ZIM archive using inverted index for fast retrieval."""
        target_path = zim_path or self.default_path

        # Check search cache first
        query_hash = hashlib.md5((target_path + query + str(max_results)).encode()).hexdigest()
        if query_hash in self._search_cache:
            results = self._search_cache[query_hash]
            return {
                "status": "ok",
                "zim_file": os.path.basename(target_path),
                "total_entries": self._index_cache.get(target_path, {}).get('entry_count', 0),
                "query": query,
                "results": results
            }

        archive, msg = self.get_archive(target_path)
        if not archive:
            return {"status": "error", "message": msg, "results": []}

        # Ensure index is built
        if target_path not in self._index_cache:
            self._build_or_load_index(target_path)

        index_data = self._index_cache.get(target_path)
        if not index_data:
            return {"status": "error", "message": "Failed to build search index", "results": []}

        # Process query
        query_terms = [t.lower() for t in re.findall(r'\b[a-zA-Z]{3,}\b', query) if len(t) > 2]
        if not query_terms:
            return {"status": "ok", "zim_file": os.path.basename(target_path),
                   "total_entries": index_data['entry_count'], "query": query, "results": []}

        # Score documents using the inverted index
        doc_scores = {}  # doc_id -> score
        inverted_index = index_data['inverted_index']

        for term in query_terms:
            if term in inverted_index:
                for doc_id, score, title, path in inverted_index[term]:
                    if doc_id not in doc_scores:
                        doc_scores[doc_id] = 0
                    doc_scores[doc_id] += score

        # Sort by score and get top results
        sorted_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)

        # Get the actual results
        results = []
        for doc_id, score in sorted_docs[:max_results]:
            try:
                e = archive._get_entry_by_id(doc_id)
                path = e.path or ""
                title = e.title or ""

                item = e.get_item()
                if item.size < 1000 or item.mimetype != "text/html":
                    continue

                html_content = bytes(item.content).decode("utf-8", errors="ignore")
                text_content = clean_html(html_content)
                snippet = text_content[:400] + "..." if len(text_content) > 400 else text_content

                results.append({
                    "title": title or path,
                    "path": path,
                    "size": item.size,
                    "snippet": snippet,
                    "score": round(score, 2)
                })
            except Exception:
                continue

        # Cache the results
        self._search_cache[query_hash] = results
        self._save_search_cache()

        return {
            "status": "ok",
            "zim_file": os.path.basename(target_path),
            "total_entries": index_data['entry_count'],
            "query": query,
            "results": results
        }

    def _load_search_cache(self):
        """Load search cache from disk."""
        try:
            if os.path.exists(SEARCH_CACHE_FILE):
                with open(SEARCH_CACHE_FILE, 'r', encoding='utf-8') as f:
                    self._search_cache = json.load(f)
        except Exception:
            self._search_cache = {}

    def _save_search_cache(self):
        """Save search cache to disk."""
        try:
            # Limit cache size to prevent excessive disk usage
            if len(self._search_cache) > 1000:
                # Keep only recent entries
                keys = list(self._search_cache.keys())
                keys_to_remove = keys[:-500]  # Remove oldest 500 entries
                for key in keys_to_remove:
                    del self._search_cache[key]

            with open(SEARCH_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._search_cache, f)
        except Exception:
            pass  # Ignore cache save errors

    def close_archive(self, path: Optional[str] = None):
        """Close a specific ZIM archive or all archives."""
        target_path = path or self.default_path
        if target_path in self._archives:
            del self._archives[target_path]
            # Note: libzim doesn't have an explicit close method, but removing reference helps GC

    def close_all(self):
        """Close all ZIM archives."""
        self._archives.clear()


# Global singleton instance
zim_engine = ZIMReaderEngine()

# Cleanup function for application shutdown
def cleanup_zim_resources():
    """Clean up ZIM resources when application shuts down."""
    zim_engine.close_all()


if __name__ == "__main__":
    # Test the ZIM reader
    result = zim_engine.search("python", max_results=3)
    print("Search result:", result)
