from pathlib import Path

from services.llm import call_llm

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def run_trader(image_path: str) -> str:
    prompt = (_PROMPTS / "trader.txt").read_text(encoding="utf-8")
    return call_llm(prompt, image_path, role="trader")
