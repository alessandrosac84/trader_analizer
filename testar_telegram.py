"""
Teste rápido de notificação Telegram.
Execute com: python testar_telegram.py
"""
import os
from pathlib import Path

# Carrega o .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    print("✅ .env carregado")
except ImportError:
    print("⚠️  python-dotenv não instalado, lendo .env manualmente...")

token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
chat_id = os.getenv("TELEGRAM_CHAT_ID",   "").strip()

print(f"Token : {'OK (' + token[:15] + '...)' if token else 'VAZIO ❌'}")
print(f"ChatID: {chat_id if chat_id else 'VAZIO ❌'}")

if not token or not chat_id:
    print("\n❌ Variáveis não encontradas. Verifique o arquivo .env")
    input("Pressione Enter para sair...")
    exit(1)

# Envia mensagem de teste
import requests

print("\nEnviando mensagem de teste...")
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id":    chat_id,
            "text":       "🤖 <b>TradeAI — Teste de conexão!</b>\n\nSe você recebeu esta mensagem, as notificações estão funcionando ✅",
            "parse_mode": "HTML",
        },
        timeout=10,
        verify=False,
    )
    if r.ok:
        print("✅ Mensagem enviada com sucesso! Verifique o Telegram.")
    else:
        print(f"❌ Erro da API Telegram: {r.status_code}")
        print(r.text)
except Exception as e:
    print(f"❌ Erro de conexão: {e}")


# Teste de trade executado
print("\nEnviando notificação de trade executado...")
from services.telegram_notifier import notify_trade_executed
notify_trade_executed(
    tv_symbol    = "BMFBOVESPA:WIN1!",
    acao         = "COMPRA",
    score        = 9,
    entry_price  = 134520,
    sl           = 133920,
    tp1          = 135120,
    order_id     = 123456,
    ai_veredito  = "APROVADO",
    ai_confianca = 87,
)
print("✅ Trade executado enviado!")

import time; time.sleep(1)

# Teste de trade fechado com gain
print("\nEnviando notificação de trade fechado (GAIN)...")
from services.telegram_notifier import notify_trade_closed
notify_trade_closed(
    tv_symbol    = "BMFBOVESPA:WIN1!",
    acao         = "COMPRA",
    entry_price  = 134520,
    exit_price   = 135120,
    close_reason = "TP1",
    pnl_pts      = 600,
    pnl_brl      = 360.00,
)
print("✅ Trade fechado (GAIN) enviado!")

time.sleep(1)

# Teste de trade fechado com stop
print("\nEnviando notificação de trade fechado (STOP)...")
notify_trade_closed(
    tv_symbol    = "BMFBOVESPA:WIN1!",
    acao         = "VENDA",
    entry_price  = 134520,
    exit_price   = 133920,
    close_reason = "STOP",
    pnl_pts      = -600,
    pnl_brl      = -360.00,
)
print("✅ Trade fechado (STOP) enviado!")

time.sleep(1)

# Teste do resumo periódico (formato real)
print("\nEnviando resumo de exemplo...")
import sys
sys.path.insert(0, str(Path(__file__).parent))

# Simula stats reais
stats_exemplo = {
    "total":         8,
    "wins":          5,
    "losses":        3,
    "bloqueados_ia": 2,
    "win_rate_pct":  62,
    "pnl_total_pts": 320,
    "pnl_total_brl": 192.00,
}

from services.telegram_notifier import notify_daily_summary
notify_daily_summary(stats_exemplo, tv_symbol="BMFBOVESPA:WIN1!")
print("✅ Resumo enviado! Verifique o Telegram.")


# Teste de notificação de auto-trade pausado via Telegram
print("\nEnviando notificação de pause remoto (como chegaria após /stop)...")
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={
        "chat_id": chat_id,
        "text": (
            "🛑 <b>Auto-Trade DESATIVADO</b>\n\n"
            "Nenhum novo trade automático será aberto.\n"
            "Use /ativar para reativar quando quiser."
        ),
        "parse_mode": "HTML",
    },
    timeout=10,
    verify=False,
)
print("✅ Enviado!")
print("\n💡 Quando o Flask estiver rodando, teste mandando /stop, /status ou /ativar direto no Telegram!")

input("\nPressione Enter para sair...")
