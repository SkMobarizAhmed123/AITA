from config import BASE_DIR
import os
import re
import json
import hashlib
from typing import List, Dict, Optional
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer
import faiss
import pypdf
import docx

# ── Config ──
RAG_INDEX_PATH = os.path.join(os.environ.get("VARIC_DIR", str(BASE_DIR)), "rag_index.faiss")
RAG_META_PATH = RAG_INDEX_PATH.replace(".faiss", "_meta.pkl")
EMBEDDING_MODEL = "all-MiniLM-L6-v2"   # ~80MB, fast CPU model
CHUNK_SIZE = 500                       # characters
CHUNK_OVERLAP = 50

class RAGEngine:
    def __init__(self):
        self.model = SentenceTransformer(EMBEDDING_MODEL)
        self.dimension = self.model.get_sentence_embedding_dimension()
        self.index = None
        self.metadata = []  # list of dicts: {"file": str, "text": str, "hash": str}
        self.seen_hashes = set()  # O(1) deduplication cache
        self._load_or_init()

    def _load_or_init(self):
        if os.path.exists(RAG_INDEX_PATH) and os.path.exists(RAG_META_PATH):
            self.index = faiss.read_index(RAG_INDEX_PATH)
            with open(RAG_META_PATH, "rb") as f:
                self.metadata = json.load(f)
            self.seen_hashes = {m["hash"] for m in self.metadata if "hash" in m}
            print(f"[RAG] Loaded index with {len(self.metadata)} chunks")
        else:
            # Using Inner Product for Cosine Similarity with normalized vectors
            self.index = faiss.IndexFlatIP(self.dimension)
            self.metadata = []
            self.seen_hashes = set()
            print("[RAG] New empty index created")

    def _save(self):
        faiss.write_index(self.index, RAG_INDEX_PATH)
        with open(RAG_META_PATH, "wb") as f:
            json.dump(self.metadata, f)

    def _chunk_text(self, text: str, rel_path: str) -> List[Dict]:
        """Split text into overlapping chunks."""
        chunks = []
        for i in range(0, len(text), CHUNK_SIZE - CHUNK_OVERLAP):
            chunk = text[i:i + CHUNK_SIZE].strip()
            if len(chunk) < 30:  # skip tiny noise fragments
                continue
            chunks.append({
                "file": rel_path,
                "text": chunk,
                "start": i,
            })
        return chunks

    def _extract_text(self, file_path: str) -> Optional[str]:
        """Extract text from .txt, .md, .pdf, .docx."""
        ext = Path(file_path).suffix.lower()
        try:
            if ext in [".txt", ".md"]:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            elif ext == ".pdf":
                reader = pypdf.PdfReader(file_path)
                return "\n".join([page.extract_text() or "" for page in reader.pages])
            elif ext == ".docx":
                doc = docx.Document(file_path)
                return "\n".join([p.text for p in doc.paragraphs])
        except Exception as e:
            print(f"[RAG] Skipping {file_path}: {e}")
        return None

    def index_folder(self, folder_path: str):
        """Walk folder, extract text, chunk, and add to index."""
        folder_path = os.path.abspath(folder_path)
        new_chunks = []
        
        for root, _, files in os.walk(folder_path):
            for file in files:
                if file.startswith("~") or file.startswith("."):  # skip temp / hidden files
                    continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, folder_path)
                
                text = self._extract_text(full_path)
                if not text:
                    continue
                
                text = re.sub(r'\s+', ' ', text).strip()
                chunks = self._chunk_text(text, rel_path)
                
                for ch in chunks:
                    h = hashlib.md5(ch["text"].encode("utf-8")).hexdigest()
                    if h not in self.seen_hashes:  # Fast O(1) set lookup
                        ch["hash"] = h
                        self.seen_hashes.add(h)
                        new_chunks.append(ch)

        if not new_chunks:
            print("[RAG] No new documents or chunks found.")
            return

        print(f"[RAG] Adding {len(new_chunks)} new chunks to vector store...")
        texts = [ch["text"] for ch in new_chunks]
        
        # Normalize embeddings for Cosine Similarity
        embeddings = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
        self.index.add(np.array(embeddings).astype("float32"))
        
        self.metadata.extend(new_chunks)
        self._save()
        print(f"[RAG] Index updated successfully! Total chunks: {len(self.metadata)}")

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """Return top-k relevant chunks sorted by similarity."""
        if len(self.metadata) == 0:
            return []
        
        q_emb = self.model.encode([query], normalize_embeddings=True)
        scores, indices = self.index.search(np.array(q_emb).astype("float32"), top_k)
        
        results = []
        for idx, score in zip(indices[0], scores[0]):
            if 0 <= idx < len(self.metadata):
                results.append({
                    "file": self.metadata[idx]["file"],
                    "text": self.metadata[idx]["text"],
                    "score": round(float(score), 4),
                })
        return results

    def format_context(self, results: List[Dict]) -> str:
        """Turn search results into a prompt-friendly context."""
        if not results:
            return ""
        lines = ["Relevant excerpts from your indexed documents:\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] From '{r['file']}' (Relevance: {r['score']}):\n{r['text']}\n")
        return "\n".join(lines)
