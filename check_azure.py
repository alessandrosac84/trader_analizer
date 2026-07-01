"""
check_azure.py — Verifica se o gate de IA (Azure OpenAI) esta operante.

Por que existe: o validador de sinais agora FALHA FECHADO (v6). Se o Azure
estiver fora do ar ou mal configurado, o auto-trade do Monitor MT5 fica PARADO.
Este script deixa isso visivel/ativo em vez de silencioso.

Uso:
  python check_azure.py            # imprime status e sai (exit 0 = ok, 1 = falha)
  python check_azure.py --telegram # alem de imprimir, alerta no Telegram se estiver fora

Dica: agende para rodar a cada 5-10 min (Agendador do Windows) para monitorar.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from services.signal_validator_ai import check_ai_health


def main() -> int:
    want_telegram = "--telegram" in sys.argv
    h = check_ai_health()

    print("─" * 48)
    print("  HEALTH-CHECK — Gate de IA (Azure OpenAI)")
    print("─" * 48)
    print(f"  Configurado : {h['configured']}")
    print(f"  Operante    : {'SIM ✅' if h['ok'] else 'NAO ❌'}")
    if h.get("latency_ms") is not None:
        print(f"  Latencia    : {h['latency_ms']} ms")
    if h.get("model"):
        print(f"  Deployment  : {h['model']}")
    if h.get("error"):
        print(f"  Erro        : {h['error']}")
    print("─" * 48)

    if h["ok"]:
        print("  Auto-trade do Monitor pode operar (gate de IA ativo).")
    else:
        print("  ⚠️  ATENCAO: com o gate fail-closed, o auto-trade do Monitor")
        print("      NAO vai executar enquanto a IA estiver fora.")
        if want_telegram:
            try:
                from services.telegram_notifier import _send as _tg_send
                _tg_send(
                    "⚠️ <b>Gate de IA FORA DO AR</b>\n"
                    f"Erro: {h.get('error')}\n"
                    "O auto-trade do Monitor MT5 esta PARADO (fail-closed) ate a IA voltar."
                )
                print("  (alerta enviado ao Telegram)")
            except Exception as e:
                print(f"  (falha ao enviar Telegram: {e})")

    return 0 if h["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
