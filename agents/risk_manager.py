from pathlib import Path

from services.llm import call_llm

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def run_risk_manager(image_path: str, trader_output: str, validator_output: str) -> str:
    prompt_template = (_PROMPTS / "risk_manager.txt").read_text(encoding="utf-8")
    prompt = (
        prompt_template.replace("{TRADER_OUTPUT}", str(trader_output)).replace(
            "{VALIDATOR_OUTPUT}", str(validator_output)
        )
    )
    return call_llm(prompt, image_path, role="risk_manager")
