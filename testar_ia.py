"""
Testa a conexao com Azure OpenAI e valida 5 cenarios de trade.
Execute: python testar_ia.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

from services.signal_validator_ai import validate_signal_with_ai

CENARIOS = [
    ("1/5 - VENDA forte (tendencia baixista clara — esperado: EXECUTAR)", "VENDA", -8, {
        "acao":"VENDA","score":-8,"preco_atual":171110,"entrada":171110,
        "stop":171735,"tp1":170200,"tp2":169500,"tp3":168000,
        "rsi":28.5,"adx":32.1,"plus_di":14.2,"minus_di":38.7,
        "ema9":171350,"ema21":171800,"ema50":172500,"ema200":173800,
        "vwap":171600,"macd_hist":-0.0042,"bb_upper":172400,"bb_lower":170100,
        "atr":420,"suporte":170050,"resistencia":171800,"htf_trend":"baixa",
        "sinais":["EMA9 < EMA21 [-1]","Preco abaixo EMA200 [-2]","RSI sobrevenda (28.5) [+2]",
                  "MACD histograma negativo [-1]","ADX 32.1 tendencia forte","1h baixa [-1]"],
    }),
    ("2/5 - COMPRA moderada (acima VWAP, 1h bullish — esperado: EXECUTAR)", "COMPRA", 5, {
        "acao":"COMPRA","score":5,"preco_atual":174200,"entrada":174200,
        "stop":173700,"tp1":175000,"tp2":175800,"tp3":177000,
        "rsi":52.3,"adx":25.8,"plus_di":28.4,"minus_di":18.1,
        "ema9":174100,"ema21":173800,"ema50":173200,"ema200":172500,
        "vwap":173900,"macd_hist":0.0021,"bb_upper":175200,"bb_lower":172800,
        "atr":380,"suporte":173700,"resistencia":175100,"htf_trend":"alta",
        "sinais":["EMA9 > EMA21 [+1]","Preco acima EMA200 [+2]","RSI neutro [0]",
                  "MACD positivo [+1]","1h alta [+1]"],
    }),
    ("3/5 - VENDA arriscada (stop apertado, ADX fraco — esperado: BLOQUEAR/AGUARDAR)", "VENDA", -4, {
        "acao":"VENDA","score":-4,"preco_atual":172500,"entrada":172500,
        "stop":172620,"tp1":172200,"tp2":171800,"tp3":171000,
        "rsi":48.1,"adx":16.5,"plus_di":22.1,"minus_di":24.3,
        "ema9":172480,"ema21":172510,"ema50":172600,"ema200":173100,
        "vwap":172550,"macd_hist":-0.0008,"bb_upper":173200,"bb_lower":171800,
        "atr":350,"suporte":172100,"resistencia":172800,"htf_trend":"baixa",
        "sinais":["EMA9 < EMA21 [-1]","ADX fraco (16.5) - range [filtro]","RSI neutro [0]","MACD negativo [-1]"],
    }),
    ("4/5 - COMPRA contra tendencia (abaixo EMA200, 1h bearish — esperado: BLOQUEAR)", "COMPRA", 4, {
        "acao":"COMPRA","score":4,"preco_atual":170800,"entrada":170800,
        "stop":170300,"tp1":171600,"tp2":172200,"tp3":173000,
        "rsi":31.0,"adx":22.4,"plus_di":26.8,"minus_di":19.2,
        "ema9":170900,"ema21":170700,"ema50":171500,"ema200":172800,
        "vwap":171200,"macd_hist":0.0015,"bb_upper":171800,"bb_lower":169900,
        "atr":400,"suporte":170100,"resistencia":171600,"htf_trend":"baixa",
        "sinais":["EMA9 > EMA21 [+1]","Preco ABAIXO EMA200 [-2]","RSI sobrevenda (31) [+2]",
                  "1h baixa [-1]","Sinal misto - threshold elevado [filtro]"],
    }),
    ("5/5 - VENDA perfeita (todos indicadores alinhados — esperado: EXECUTAR alta confianca)", "VENDA", -10, {
        "acao":"VENDA","score":-10,"preco_atual":173800,"entrada":173800,
        "stop":174300,"tp1":172900,"tp2":172000,"tp3":170500,
        "rsi":72.4,"adx":38.2,"plus_di":12.1,"minus_di":44.5,
        "ema9":173600,"ema21":174100,"ema50":174800,"ema200":175500,
        "vwap":174200,"macd_hist":-0.0089,"bb_upper":174600,"bb_lower":172800,
        "atr":450,"suporte":172700,"resistencia":174400,"htf_trend":"baixa",
        "sinais":["EMA9 < EMA21 [-1]","Preco abaixo EMA200 [-2]","RSI sobrecompra (72.4) [-2]",
                  "MACD cruzou abaixo [-2]","ADX 38.2 forte","-DI domina","1h baixa [-1]","Engolfo Baixa [-2]"],
    }),
]

print()
print("=" * 65)
print("  TESTE DA IA — Azure OpenAI (5 cenarios)")
print("=" * 65)

ia_funcionou = False
for label, acao, score, signal in CENARIOS:
    print(f"\n[{label}]")
    print(f"  Acao: {acao} | Score: {score}")
    try:
        r = validate_signal_with_ai(signal, tv_symbol="BMFBOVESPA:WIN1!", interval="15")
        ia_ok = r.get("ia_usada", False)
        if ia_ok and "Erro na validacao" not in r.get("motivo",""):
            ia_funcionou = True
            status = "IA REAL"
        else:
            status = "FALLBACK"
        veredito = r.get("veredito", "?")
        conf     = r.get("confianca", 0)
        motivo   = (r.get("motivo") or "")[:100]
        aprovado = r.get("aprovado", False)
        if aprovado and veredito == "EXECUTAR":
            icone = "✅ EXECUTAR"
        elif veredito == "AGUARDAR":
            icone = "⏳ AGUARDAR"
        else:
            icone = "🚫 BLOQUEAR"
        print(f"  [{status}] => {icone} ({conf}%)")
        print(f"  Motivo: {motivo}")
        for a in (r.get("alertas") or []):
            print(f"  Alerta: {a}")
        if r.get("tp1_sugerido"):
            print(f"  TP1 sugerido pela IA: {r['tp1_sugerido']}")
    except Exception as e:
        import traceback
        print(f"  ERRO: {e}")
        traceback.print_exc()

print()
print("=" * 65)
if ia_funcionou:
    print("  RESULTADO: IA CONECTADA E FUNCIONANDO! ✅")
else:
    print("  RESULTADO: IA nao conectou — verifique o terminal acima.")
print("=" * 65)
print()
input("Pressione Enter para sair...")
