"""
config.py — Central configuration for the AITA from-scratch LLM pipeline.

Edit the PATHS section to match your machine, then pick a MODEL PRESET.
Everything else (tokenizer, data prep, training, generation) reads from here.
"""
import os
import torch

# ──────────────────────────────────────────────────────────────────────────────
# PATHS — edit these for your machine
# ──────────────────────────────────────────────────────────────────────────────
# The JSONL produced by extract_zim.py ({"title": ..., "text": ...} per line)
DATASET_JSONL = r"D:\wikipedia_train_dataset.jsonl"

# Where all pipeline artifacts live (tokenizer, binary shards, checkpoints)
WORK_DIR = r"D:\zaor_llm"

TOKENIZER_DIR = os.path.join(WORK_DIR, "tokenizer")
DATA_DIR = os.path.join(WORK_DIR, "data")
CHECKPOINT_DIR = os.path.join(WORK_DIR, "checkpoints")

TOKENIZER_FILE = os.path.join(TOKENIZER_DIR, "zaor_bpe.json")
TRAIN_BIN = os.path.join(DATA_DIR, "train.bin")
VAL_BIN = os.path.join(DATA_DIR, "val.bin")
CHECKPOINT_FILE = os.path.join(CHECKPOINT_DIR, "zaor_ckpt.pt")

# ──────────────────────────────────────────────────────────────────────────────
# TOKENIZER
# ──────────────────────────────────────────────────────────────────────────────
VOCAB_SIZE = 8192          # total vocab including 256 byte tokens + specials
TOKENIZER_SAMPLE_MB = 80   # how many MB of raw text to train BPE on (sampled)

# Special token ids (reserved at the top of the vocab)
# <eos> is used as the document separator during data prep.
SPECIAL_TOKENS = ["<pad>", "<unk>", "<bos>", "<eos>"]

# ──────────────────────────────────────────────────────────────────────────────
# DATA PREP
# ──────────────────────────────────────────────────────────────────────────────
VAL_FRACTION = 0.0005      # fraction of documents held out for validation
MAX_DOCS = None            # cap documents for a quick test run, e.g. 50_000

# ──────────────────────────────────────────────────────────────────────────────
# MODEL PRESETS
# ──────────────────────────────────────────────────────────────────────────────
# "cpu"   ~15M params — realistic to pretrain on a modern CPU over days
# "small" ~50M params — needs a GPU (6GB+ VRAM)
# "base"  ~124M params — needs a GPU (8GB+ VRAM)
PRESET = "cpu"

_PRESETS = {
    "cpu":   dict(n_layer=6,  n_head=6,  n_embd=384, block_size=256),
    "small": dict(n_layer=8,  n_head=8,  n_embd=512, block_size=512),
    "base":  dict(n_layer=12, n_head=12, n_embd=768, block_size=1024),
}

MODEL = _PRESETS[PRESET]
N_LAYER = MODEL["n_layer"]
N_HEAD = MODEL["n_head"]
N_EMBD = MODEL["n_embd"]
BLOCK_SIZE = MODEL["block_size"]
DROPOUT = 0.0              # 0.0 for pretraining on a huge corpus

# ──────────────────────────────────────────────────────────────────────────────
# TRAINING
# ──────────────────────────────────────────────────────────────────────────────
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

BATCH_SIZE = 8 if DEVICE == "cpu" else 32
GRAD_ACCUM_STEPS = 4       # effective batch = BATCH_SIZE * GRAD_ACCUM_STEPS
MAX_STEPS = 200_000        # total optimizer steps (stop earlier any time; resume works)
LEARNING_RATE = 6e-4
MIN_LR = 6e-5
WARMUP_STEPS = 2_000
WEIGHT_DECAY = 0.1
GRAD_CLIP = 1.0
BETA1, BETA2 = 0.9, 0.95

EVAL_INTERVAL = 500        # steps between validation-loss evaluations
EVAL_BATCHES = 20          # batches per evaluation
CHECKPOINT_INTERVAL = 500  # steps between checkpoint saves
LOG_INTERVAL = 10          # steps between console log lines

# CPU niceties
NUM_THREADS = os.cpu_count() or 4


def ensure_dirs():
    for d in (WORK_DIR, TOKENIZER_DIR, DATA_DIR, CHECKPOINT_DIR):
        os.makedirs(d, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# GLOBAL APPLICATION PATHS (Dynamic & Portable)
# ──────────────────────────────────────────────────────────────────────────────
from pathlib import Path
import os

BASE_DIR = Path(__file__).parent.resolve()

_onedrive_desktop = Path.home() / 'OneDrive' / 'Desktop'
if _onedrive_desktop.exists():
    DESKTOP_DIR = _onedrive_desktop
else:
    DESKTOP_DIR = Path.home() / 'Desktop'

USER_DATA_DIR = BASE_DIR / 'User_Data'
PROJECTS_DIR = BASE_DIR / 'projects'

# Ensure global directories exist
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
