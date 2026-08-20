# AITA LLM — From-Scratch Training Pipeline

A complete, self-contained pipeline to train your **own** GPT language model from
scratch on your 11.7 GB Wikipedia ZIM dump — no Qwen, no Ollama, no external APIs.

Everything is a real implementation: a byte-level BPE tokenizer, a nanoGPT-style
decoder-only transformer, memory-mapped data loading, cosine LR schedule with
warmup, gradient accumulation, and automatic checkpoint/resume.

## Files

| File | What it does |
|------|--------------|
| `config.py` | All paths + hyperparameters. **Edit this first.** |
| `extract_zim.py` | (Your existing script) ZIM → `wikipedia_train_dataset.jsonl` |
| `train_tokenizer.py` | Trains a byte-level BPE tokenizer on the JSONL |
| `prepare_data.py` | Tokenizes the JSONL into packed `train.bin` / `val.bin` |
| `model.py` | The `AITAGPT` transformer (from scratch) |
| `train.py` | Pretrains the model, checkpoints, resumes |
| `zaor_engine.py` | Loads a checkpoint and generates / streams text |

## Setup

```bash
pip install -r requirements.txt
```

Then open `config.py` and set:
- `DATASET_JSONL` — path to the JSONL from `extract_zim.py`
- `WORK_DIR` — where tokenizer/data/checkpoints are written
- `PRESET` — leave as `"cpu"` since you're training on CPU

## Run the pipeline (in order)

```bash
# 1. Extract Wikipedia text (your existing script)
python extract_zim.py

# 2. Train the tokenizer  (minutes)
python train_tokenizer.py

# 3. Pack the corpus into token shards  (tens of minutes, one time)
python prepare_data.py

# 4. Pretrain the model  (long-running; Ctrl+C any time, re-run to resume)
python train.py

# 5. Try it out
python zaor_engine.py "Title: The Moon\n\n"
```

## About CPU training (important expectations)

You chose CPU-only, so `config.PRESET = "cpu"` builds a ~15M-parameter model.
This is deliberately small so it can actually make progress on a CPU.

- Watch the `[sample]` lines printed during training. Early on it's gibberish;
  after enough steps it starts producing Wikipedia-flavored English.
- A model this size on CPU will read as a fluent-but-limited "baby" model —
  it will **not** match Qwen 3B's reasoning. That is the expected trade-off of a
  fully from-scratch CPU build.
- Training is measured in days, not minutes. Because checkpoints save every
  `CHECKPOINT_INTERVAL` steps and `train.py` auto-resumes, you can stop and
  restart freely (even across reboots).

### Tips to see results faster
- In `config.py`, set `MAX_DOCS = 50_000` for a quick end-to-end smoke test
  before committing to the full corpus.
- Lower `BLOCK_SIZE` (e.g. 128) to speed up CPU steps at the cost of context.
- If you ever add an NVIDIA GPU, just change `PRESET` to `"small"` or `"base"`,
  reinstall the CUDA build of PyTorch, and re-run — the code auto-detects CUDA.

## Wiring it into your app (optional)

`zaor_engine.py` exposes a lazy singleton via `get_engine()` with `.generate()`
and `.stream()`. To replace the old Ollama/qwen path in `main.py`:

```python
from zaor_engine import get_engine

def process_command_stream(message: str):
    for delta in get_engine().stream(message, max_new_tokens=200):
        yield json.dumps({"type": "chunk", "content": delta}) + "\n"
    yield json.dumps({"type": "done"}) + "\n"
```

That removes every `query_ollama*` call and the `qwen2.5:3b` dependency entirely.
Say the word and I'll rewrite `custom_engine.py` and `main.py` to drop Ollama
completely and stream from your trained checkpoint.
