from config import BASE_DIR
import os
import pickle
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
PDF_FOLDER          = str(BASE_DIR / "research_neet")
INDEX_FILE          = str(BASE_DIR / "index.pkl")
MAX_WORDS_PER_CHUNK = 200

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def find_all_pdfs(folder: str) -> list:
    pdf_paths = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".pdf"):
                pdf_paths.append(os.path.join(root, f))
    return sorted(pdf_paths)

def extract_text(path: str) -> str:
    try:
        reader = PdfReader(path)
        pages  = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip()
    except Exception as e:
        print(f"  ⚠ Could not read file: {e}")
        return ""

def chunk_text(text: str, source: str, max_words: int = MAX_WORDS_PER_CHUNK):
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, metadata = [], []
    for para in paragraphs:
        words = para.split()
        for i in range(0, len(words), max_words):
            chunks.append(" ".join(words[i:i + max_words]))
            metadata.append(source)
    return chunks, metadata

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
print(f"🔍 Scanning: {PDF_FOLDER}")
pdf_paths = find_all_pdfs(PDF_FOLDER)

if not pdf_paths:
    print(f"❌ No PDFs found anywhere in {PDF_FOLDER}")
    print("   Check the folder path and make sure PDFs exist inside subfolders.")
    exit(1)

print(f"📄 Found {len(pdf_paths)} PDFs\n")

all_chunks, all_metadata = [], []
skipped = 0

for i, path in enumerate(pdf_paths, 1):
    label = os.path.relpath(path, PDF_FOLDER)  # e.g. Biology\chapter1.pdf
    print(f"[{i}/{len(pdf_paths)}] {label}")

    text = extract_text(path)

    if not text:
        print(f"  ⚠ Skipped — no text found (possibly a scanned/image PDF)")
        skipped += 1
        continue

    chunks, metadata = chunk_text(text, source=label)
    all_chunks.extend(chunks)
    all_metadata.extend(metadata)
    print(f"  ✓ {len(chunks)} chunks")

print(f"\n{'─'*50}")
print(f"✅ Processed : {len(pdf_paths) - skipped}/{len(pdf_paths)} PDFs")
print(f"⚠  Skipped   : {skipped} PDFs")
print(f"📦 Total chunks: {len(all_chunks)}")

if not all_chunks:
    print("\n❌ No text extracted from any PDF.")
    print("   Your PDFs may be scanned images. Let me know and I'll add OCR support.")
    exit(1)

print("\n⚙ Building TF-IDF index...")
vectorizer    = TfidfVectorizer(stop_words="english")
chunk_vectors = vectorizer.fit_transform(all_chunks)

with open(INDEX_FILE, "wb") as f:
    pickle.dump({
        "chunks":     all_chunks,
        "metadata":   all_metadata,
        "vectorizer": vectorizer,
        "vectors":    chunk_vectors,
    }, f)

print(f"✅ index.pkl saved → {INDEX_FILE}")
print(f"\n🚀 You can now run: uvicorn main:app --reload")
