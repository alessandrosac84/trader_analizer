"""
backtest_crypto.py — Backtest do motor do Monitor CRYPTO usando DADOS DO MT5.

Roda o MESMO motor de sinal do Monitor Crypto (services.crypto_analysis.analyze)
sobre candles reais do MT5 (conta ICMarkets) e mede PROFITABILIDADE em múltiplos
de risco (R), não só win rate.

O foco deste backtest é responder UMA pergunta:
  "Um filtro anti-lateral (ADX) + alinhamento MACRO de verdade (tendência do H1)
   melhora o resultado do crypto?"

Hoje o motor já exige ADX ≥ adx_min e alinhamento htf — MAS esse htf é só a
EMA50×EMA200 no MESMO timeframe de operação (ex.: M15). Isso engana em range:
a EMA50 fica acima da EMA200 e o robô entra COMPRA enquanto o preço já virou.
Aqui adicionamos o MACRO REAL (tendência do H1) e comparamos BASE × COM FILTRO.

⚠️ Precisa rodar NA SUA MÁQUINA, com o MetaTrader 5 (ICMarkets) aberto e logado.

Uso:
  python backtest_crypto.py                       # BTCUSD, M15, macro H1
  python backtest_crypto.py --symbol ETHUSD
  python backtest_crypto.py --tf 5 --bars 8000    # opera em M5
  python backtest_crypto.py --adx-min 25          # corte anti-lateral mais duro
  python backtest_crypto.py --macro-tf 240        # macro no H4 em vez do H1
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    import pandas as pd
except ImportError:
    print("pandas não instalado. Execute: pip install pandas")
    sys.exit(1)

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 não instalado. Rode na máquina Windows com o MT5 aberto.")
    sys.exit(1)

from services.crypto_analysis import analyze
from services.crypto_config import cfg_for, get_symbols

_TF = {
    "1": mt5.TIMEFRAME_M1, "5": mt5.TIMEFRAME_M5, "15": mt5.TIMEFRAME_M15,
    "30": mt5.TIMEFRAME_M30, "60": mt5.TIMEFRAME_H1, "1h": mt5.TIMEFRAME_H1,
    "240": mt5.TIMEFRAME_H4, "4h": mt5.TIMEFRAME_H4,
}


def _mt5_init() -> bool:
    """Inicializa o MT5 usando o perfil CRYPTO (ICMarkets) do .env, com fallback."""
    if mt5.terminal_info() is not None:
        return True
    # perfil crypto tem prioridade (MT5_CRYPTO_*), cai pro MT5_* padrão
    def _g(k, d=""):
        return os.getenv("MT5_CRYPTO_" + k) or os.getenv("MT5_" + k, d)
    login = int(_g("LOGIN", "0") or 0)
    password = _g("PASSWORD", "")
    server = _g("SERVER", "")
    path = _g("PATH", "")
    kwargs = {}
    if path and os.path.exists(path):
        kwargs["path"] = path
    _mq = {"metaquotes-demo", "metaquotes-demo2"}
    if login and password and server and server.lower() not in _mq:
        kwargs["login"] = login
        kwargs["password"] = password
        kwargs["server"] = server
    return mt5.initialize(**kwargs)


def _fetch(symbol: str, tf_key: str, bars: int):
    """Puxa candles do MT5 como lista de dicts (formato que analyze() espera)."""
    tf = _TF.get(str(tf_key), mt5.TIMEFRAME_M15)
    if not mt5.symbol_select(symbol, True):
        print(f"  ⚠️  não consegui selecionar {symbol} no Market Watch")
        return None
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        print(f"  ⚠️  sem candles para {symbol} (erro: {mt5.last_error()})")
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _macro_series(macro_df):
    """Tendência do timeframe MACRO por candle: 'alta'/'baixa'/'lateral' (EMA50×200)."""
    c = macro_df["close"]
    e50, e200 = _ema(c, 50), _ema(c, 200)
    trend = pd.Series("lateral", index=macro_df.index)
    trend[e50 > e200] = "alta"
    trend[e50 < e200] = "baixa"
    return pd.Series(trend.values, index=macro_df["time"].values)


def _macro_at(macro_trend, ts):
    """Tendência macro vigente no instante ts (último candle macro fechado ≤ ts)."""
    idx = macro_trend.index[macro_trend.index <= ts]
    if len(idx) == 0:
        return "lateral"
    return macro_trend.loc[idx[-1]]


def _evaluate(acao, entrada, stop, tp1, future):
    """Simula candle a candle: bateu no stop ou no TP1 primeiro? Retorna (outcome, R)."""
    risk = abs(entrada - stop) if stop else 0.0
    if risk <= 0:
        return "OPEN", 0.0
    for _, row in future.iterrows():
        hi, lo = row["high"], row["low"]
        if acao == "COMPRA":
            if lo <= stop:
                return "LOSS", -1.0
            if hi >= tp1:
                return "WIN", (tp1 - entrada) / risk
        else:
            if hi >= stop:
                return "LOSS", -1.0
            if lo <= tp1:
                return "WIN", (entrada - tp1) / risk
    # não resolveu: marca a mercado (mtm) no fim da janela
    last = future["close"].iloc[-1]
    mtm = (last - entrada) if acao == "COMPRA" else (entrada - last)
    return "OPEN", (mtm / risk)


def _stats(r):
    dec = r[r.outcome.isin(["WIN", "LOSS"])]
    wins = dec[dec.outcome == "WIN"]
    losses = dec[dec.outcome == "LOSS"]
    n_dec = len(dec)
    wr = len(wins) / n_dec * 100 if n_dec else 0
    gross_win = wins["R"].clip(lower=0).sum()
    gross_loss = -losses["R"].clip(upper=0).sum()
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    exp_dec = dec["R"].mean() if n_dec else 0
    return {"n": len(r), "n_dec": n_dec, "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2), "exp_R_dec": round(exp_dec, 3),
            "total_R_dec": round(dec["R"].sum(), 1)}


def _line(label, st):
    print(f"  {label:20}: {st['n_dec']:>4} dec  |  WR {st['win_rate']:>4.1f}%  "
          f"PF {st['profit_factor']:>5}  exp {st['exp_R_dec']:+.3f}R  soma {st['total_R_dec']:+.1f}R")


def run(symbol, tf_key, bars, forward, macro_tf, adx_min):
    print(f"\n📥  {symbol}  tf={tf_key}m  macro={macro_tf}m  bars={bars}")
    df = _fetch(symbol, tf_key, bars)
    if df is None or len(df) < 220:
        print("  (candles insuficientes)")
        return None
    macro_df = _fetch(symbol, macro_tf, max(500, bars // 8))
    macro_trend = _macro_series(macro_df) if macro_df is not None else None
    print(f"✅  {len(df)} candles ({df['time'].iloc[0]} → {df['time'].iloc[-1]})")

    cfg = cfg_for(symbol)
    smin = int(cfg.get("score_min", 7))
    rows = df.to_dict("records")
    results = []
    lookback = 210  # analyze precisa de EMA200
    for i in range(lookback, len(df) - forward):
        window = rows[:i]
        sig = analyze(window, cfg)
        if sig.get("acao") not in ("COMPRA", "VENDA"):
            continue
        if abs(sig.get("score", 0)) < smin:
            continue
        e, s, t = sig.get("entrada"), sig.get("stop"), sig.get("tp1")
        if None in (e, s, t):
            continue
        future = df.iloc[i:i + forward]
        outcome, R = _evaluate(sig["acao"], e, s, t, future)
        ts = df["time"].iloc[i]
        macro = _macro_at(macro_trend, ts) if macro_trend is not None else "lateral"
        if sig["acao"] == "COMPRA":
            macro_ok = (macro == "alta")
        else:
            macro_ok = (macro == "baixa")
        results.append({
            "acao": sig["acao"], "score": sig["score"], "outcome": outcome, "R": R,
            "adx": sig.get("adx", 0.0), "macro": macro, "macro_ok": bool(macro_ok),
        })

    if not results:
        print("  (nenhum sinal com esses parâmetros)")
        return None
    r = pd.DataFrame(results)
    _report(symbol, r, macro_tf, adx_min)
    return {"symbol": symbol, "df": r}


def _report(symbol, r, macro_tf, adx_min):
    st = _stats(r)
    opn = (r.outcome == "OPEN").sum()
    ver = "🟢 LUCRATIVO" if st["exp_R_dec"] > 0.03 else ("🟡 NEUTRO" if st["exp_R_dec"] > -0.03 else "🔴 NEGATIVO")
    print(f"  Sinais: {st['n']}  |  decididos {st['n_dec']}  OPEN {opn}")
    print(f"  → {ver}   WR {st['win_rate']:.1f}%  PF {st['profit_factor']}  exp {st['exp_R_dec']:+.3f}R  soma {st['total_R_dec']:+.1f}R")

    print(f"\n  ── FILTRO anti-lateral (ADX) + MACRO real (tendência do {macro_tf}m) ──")
    _line("BASE (motor atual)", st)
    f_macro = r[r.macro_ok]
    f_adx = r[r.adx >= adx_min]
    f_both = r[r.macro_ok & (r.adx >= adx_min)]
    if len(f_macro):
        _line(f"+ MACRO {macro_tf}m alinhado", _stats(f_macro))
    if len(f_adx):
        _line(f"+ ADX≥{adx_min}", _stats(f_adx))
    if len(f_both):
        _line("+ MACRO & ADX", _stats(f_both))

    print("  varredura ADX (com macro alinhado):")
    best = None
    for th in (18, 20, 22, 25, 28, 30, 35):
        sub = r[r.macro_ok & (r.adx >= th)]
        sd = sub[sub.outcome.isin(["WIN", "LOSS"])]
        if len(sd) >= 12:
            e = sd["R"].mean()
            print(f"     ADX≥{th:<3}: exp {e:+.3f}R  PF {_stats(sub)['profit_factor']:>5}  "
                  f"WR {(sd.outcome=='WIN').mean()*100:>3.0f}%  (n={len(sd)})")
            if best is None or e > best[1]:
                best = (th, e)
    if best:
        print(f"     → melhor corte: MACRO alinhado + ADX≥{best[0]}  (exp {best[1]:+.3f}R/trade)")
    else:
        print("     (poucos trades após o filtro para medir com confiança)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backtest do motor Crypto (filtro anti-lateral + macro real)")
    ap.add_argument("--symbol", default=None, help="Símbolo (ex: BTCUSD). Vazio = todos do CRYPTO_SYMBOLS")
    ap.add_argument("--tf", default="15", help="Timeframe de operação: 1,5,15,30,60")
    ap.add_argument("--macro-tf", default="60", help="Timeframe MACRO: 60 (H1) ou 240 (H4)")
    ap.add_argument("--bars", type=int, default=6000, help="Qtd de candles a puxar")
    ap.add_argument("--forward", type=int, default=12, help="Velas à frente p/ checar TP/stop")
    ap.add_argument("--adx-min", type=int, default=25, help="Corte do filtro anti-lateral (ADX)")
    args = ap.parse_args()

    if not _mt5_init():
        print(f"❌  MT5 não inicializou: {mt5.last_error()}")
        print("   Abra o MetaTrader 5 (ICMarkets), logue, e confira MT5_CRYPTO_* no .env")
        sys.exit(1)

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = get_symbols()
    print("═" * 60)
    print(f"  BACKTEST CRYPTO — motor do Monitor  ·  {', '.join(symbols)}")
    print("═" * 60)

    summary = []
    for sym in symbols:
        out = run(sym, args.tf, args.bars, args.forward, args.macro_tf, args.adx_min)
        if out:
            base = _stats(out["df"])
            base["symbol"] = sym
            filt = _stats(out["df"][out["df"].macro_ok & (out["df"].adx >= args.adx_min)])
            base["exp_filt"] = filt["exp_R_dec"]
            base["pf_filt"] = filt["profit_factor"]
            base["n_filt"] = filt["n_dec"]
            summary.append(base)

    if summary:
        print("\n" + "═" * 60)
        print("  RESUMO — BASE (motor atual)  ×  COM FILTRO (macro + ADX)")
        for st in summary:
            fb = "🟢" if st["exp_R_dec"] > 0.03 else ("🟡" if st["exp_R_dec"] > -0.03 else "🔴")
            ff = "🟢" if st["exp_filt"] > 0.03 else ("🟡" if st["exp_filt"] > -0.03 else "🔴")
            print(f"  {st['symbol']:9}")
            print(f"     {fb} BASE  : exp {st['exp_R_dec']:+.3f}R  PF {st['profit_factor']}  WR {st['win_rate']:.0f}%  ({st['n_dec']} dec)")
            print(f"     {ff} FILTRO: exp {st['exp_filt']:+.3f}R  PF {st['pf_filt']}  ({st['n_filt']} dec)")
        print("═" * 60)
        print("\n  Se a linha FILTRO tiver expectância e PF melhores que a BASE com um")
        print("  nº de trades razoável, vale ligar o macro real no motor. Ajuste --adx-min")
        print("  e --macro-tf (60/240) para achar o melhor corte antes de mexer no motor.\n")
    mt5.shutdown()
