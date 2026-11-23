# Déjà Fix

**Self-learning RAG customer support chatbot using Gemini and SQLite. (Created using Gemini Hydrogen)**

## 1. Install
```bash
pip install -r requirements.txt
```

## 2. Set API Key (Pick One)
* **Terminal / `.env`:** `export GEMINI_API_KEY="your_key"` (or put in `.env`)
* **Direct in code:** Paste into `API_KEY` in `gemini_helper.py` *(remove before `git push`)*

## 3. Run
* **3-session demo:** `python demo.py`
* **Interactive chat:** `python chatbot.py` *(type `exit` to quit)*
* **API server:** `uvicorn server:app --port 8000` *(Swagger UI at `/docs`)*
