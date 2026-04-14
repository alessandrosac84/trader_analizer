from pathlib import Path

from services.llm import call_llm

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def run_validator(image_path: str, trader_output: str) -> str:
    prompt_template = (_PROMPTS / "validator.txt").read_text(encoding="utf-8")
    prompt = prompt_template.replace("{TRADER_OUTPUT}", str(trader_output))
    return call_llm(prompt, image_path, role="validator")
