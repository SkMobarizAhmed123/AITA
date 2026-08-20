# Varic AI - Project Definition

## Overview
**Varic** (also referred to as Zaor or AITA) is an end-to-end, locally hosted Artificial Intelligence ecosystem. Unlike wrappers that just send data to OpenAI, Varic is built to be a self-contained intelligence system featuring a custom-trained Language Model, a dedicated desktop interface, and advanced Retrieval-Augmented Generation (RAG) capabilities.

## Core Components

### 1. Custom From-Scratch LLM Engine
At the heart of the project is a custom, from-scratch generative language model pipeline (nanoGPT-style architecture).
- **Training Pipeline**: Capable of ingesting massive offline datasets (like Wikipedia ZIM dumps or custom Legal document collections) and training a Byte-Pair Encoding (BPE) tokenizer and a transformer model entirely on local hardware (`train.py`, `model.py`, `prepare_data.py`).
- **Inference Engine**: A custom engine (`zaor_engine.py`) that loads the locally trained checkpoints and streams generated text efficiently.

### 2. The Desktop App Interface
A native, high-performance desktop application built using `PySide6` (Qt for Python). 
- **Frontend**: Combines native Qt windowing with HTML/JS/CSS web views (`index.html`, `styles.css`) for a modern, sleek chat interface.
- **Backend Orchestration**: `desktop_app.py` and `main.py` handle the threading, hardware acceleration, and UI rendering, connecting the user directly to the underlying AI engines.

### 3. Advanced Memory and RAG (Retrieval-Augmented Generation)
To ensure the AI is grounded in factual data and can remember context over time, the system includes modular cognitive engines:
- **`memory_engine.py`**: A persistent memory system allowing the AI to recall past interactions and facts (backed by a local SQLite/Vector DB).
- **`rag_engine.py` / `code_rag.py`**: Systems that parse external documents, index them, and retrieve relevant snippets to feed to the LLM during generation, allowing the model to answer questions about specific files or codebases without retraining.

### 4. Specialized Utilities
- **Data Ingestion**: Tools like `zim_reader.py` and `extract_zim.py` to scrape, parse, and clean massive offline data archives for training.
- **System Integration**: Modules like `gmail_api.py` and `network_monitor.py` indicating the AI is designed to interact with external APIs and monitor system health.

## Project Goals
Varic represents a push towards **sovereign AI**—giving the user complete ownership over the model's training data, weights, and inference, wrapped in a polished desktop experience.
