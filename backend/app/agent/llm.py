"""
Chat model factory. Live runs use Gemini through langchain-google-genai (key from GEMINI_API_KEY in .env).

    python -m app.agent.llm --list-models     # which models your key can call (so GEMINI_MODEL can be set correctly)
"""
import argparse
from typing import Optional

from app.config import settings


def get_chat_model(model: Optional[str] = None):
    """Returns a Gemini chat model, or None if no key is configured."""
    if not settings.GEMINI_API_KEY:
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=model or settings.GEMINI_MODEL, api_key=settings.GEMINI_API_KEY,
                                  temperature=0, timeout=settings.AGENT_TIMEOUT_SECONDS, max_retries=2)


def list_models() -> None:
    if not settings.GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY is not set (put it in the repo-root .env).")
    from google import genai
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if "generateContent" in actions:
            print(m.name.replace("models/", ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-models", action="store_true")
    if ap.parse_args().list_models:
        list_models()
