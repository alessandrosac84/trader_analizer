"""
backtest_mt5.py — Backtest do motor do Monitor usando DADOS DO METATRADER 5.

Diferente do backtest.py (que usa Yahoo Finance, com delay e como proxy), este
puxa candles REAIS do MT5 — os mesmos dados que a plataforma usa para operar.

⚠️ Precisa rodar NA SUA MÁQUINA, com o MetaTrader 5 aberto e logado.
   (O pacote MetaTrader5 só funciona no Windows com o terminal ativo.)

Uso:
  python backtest_mt5.py                         # WIN e WDO, 15m, ~60 dias, regras novas
  python backtest_mt5.py --symbol WINV26         # um símbolo específico
  python backtest_mt5.py --tf 5 --bars 8000      # timeframe 5m, mais candles
  python backtest_mt5.py --min-score 4           # compara com o threshold antigo
  python backtest_mt5.py --list                  # lista símbolos WIN/WDO disponíveis

Compare o win rate com --min-score 4 (regra antiga) vs 7 (nova) para ver o efeito.
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
    print("MetaTrader5 não instalado. Execute: pip install MetaTrader5")
    print("(e rode este script na máquina Windows com o MT5 aberto)")
    sys.exit(1)

from services.technical_analysis import generate_signal
from services.trade_executor import SCORE_MIN  # threshold novo (7)


# ── Timeframes ──────────────────────────────────────────────────────────────
_TF = {
    "1":  mt5.TIMEFRAME_M1,  "5":  mt5.TIMEFRAME_M5,
    "15": mt5.TIMEFRAME_M15, "30": mt5.TIMEFRAME_M30,
    "60": mt5.TIMEFRAME_H1,  "1h": mt5.TIMEFRAME_H1,
}


def _mt5_init() -> bool:
    """Inicializa o MT5 reusando as credenciais do .env (mesmo padrão do bot)."""
    if mt5.terminal_info() is not None:
        return True
    login    = int(os.getenv("MT5_LOGIN", "0") or 0)
    password = os.getenv("MT5_PASSWORD", "")
    server   = os.getenv("MT5_SERVER", "")
    path     = os.getenv("MT5_PATH", "")
    kwargs = {}
    if path and os.path.exists(path):
        kwargs["path"] = path
    _mq = {"metaquotes-demo", "metaquotes-demo2"}
    if login and password and server and server.lower() not in _mq:
        kwargs["login"] = login; kwargs["password"] = password; kwargs["server"] = server
    return mt5.initialize(**kwargs)


def _resolve_symbols(user_symbol: str | None) -> list[str]:
    """Descobre os símbolos WIN/WDO ativos no Market Watch."""
    if user_symbol:
        return [user_symbol]
    env_syms = [os.getenv("WIN_MT5_SYMBOL", ""), os.getenv("WDO_MT5_SYMBOL", "")]
    env_syms = [s for s in env_syms if s]
    if env_syms:
        return env_syms
    found = []
    for pref in ("WIN", "WDO"):
        syms = mt5.symbols_get(pref + "*") or []
        # pega o de maior volume (contrato corrente)
        best, best_vol = None, -1
        for s in syms:
            info = mt5.symbol_info(s.name)
            vol = getattr(info, "volume", 0) if info else 0
            if s.name[:3] == pref and (info and info.visible) and vol >= best_vol:
                best, best_vol = s.name, vol
        if best:
            found.append(best)
    return found or ["WIN$", "WDO$"]


def _fetch(symbol: str, tf_key: str, bars: int) -> "pd.DataFrame | None":
    tf = _TF.get(tf_key, mt5.TIMEFRAME_M15)
    if not mt5.symbol_select(symbol, True):
        print(f"  ⚠️  não consegui selecionar {symbol} no Market Watch")
        return None
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        # copy_rates_from_pos estoura com counts grandes ("Invalid params"). Fallback
        # por INTERVALO DE DATAS (sem limite de contagem) e recorta os últimos `bars`.
        from datetime import datetime, timedelta
        frm = datetime.now() - timedelta(days=1000)
        to = datetime.now() + timedelta(days=1)
        rates = mt5.copy_rates_range(symbol, tf, frm, to)
        if rates is not None and len(rates) > bars:
            rates = rates[-bars:]
    if rates is None or len(rates) == 0:
        print(f"  ⚠️  sem candles para {symbol} (erro: {mt5.last_error()})")
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    vol = df["real_volume"] if df["real_volume"].sum() > 0 else df["tick_volume"]
    out = pd.DataFrame({
        "Open": df["open"], "High": df["high"], "Low": df["low"],
        "Close": df["close"], "Volume": vol,
    }, index=df.index)
    return out


def _depth(symbol: str, tf_key: str):
    """Mede o histórico disponível por INTERVALO DE DATAS (copy_rates_range) — sem o
    limite de contagem do copy_rates_from_pos (que estoura com pedidos grandes).
    Retorna (n_candles, primeiro, último) ou None. Silencioso (não polui a saída)."""
    from datetime import datetime, timedelta
    tf = _TF.get(tf_key, mt5.TIMEFRAME_M15)
    try:
        if not mt5.symbol_select(symbol, True):
            return None
        frm = datetime.now() - timedelta(days=900)     # ~2,5 anos p/ trás
        to = datetime.now() + timedelta(days=1)         # folga p/ fuso
        rates = mt5.copy_rates_range(symbol, tf, frm, to)
        if rates is None or len(rates) == 0:
            return None
        import pandas as _pd
        t = _pd.to_datetime([r[0] for r in rates], unit="s")
        return len(rates), t[0], t[-1]
    except Exception:
        return None


def _adx(df, n=14):
    """ADX (força de tendência) da janela. Retorna o último valor (0-100)."""
    try:
        h, l, c = df["High"], df["Low"], df["Close"]
        up, dn = h.diff(), -l.diff()
        plus_dm = ((up > dn) & (up > 0)) * up
        minus_dm = ((dn > up) & (dn > 0)) * dn
        tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(n).mean().replace(0, 1e-9)
        pdi = 100 * (plus_dm.rolling(n).mean() / atr)
        mdi = 100 * (minus_dm.rolling(n).mean() / atr)
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-9)
        v = dx.rolling(n).mean().iloc[-1]
        return float(v) if v == v else 0.0   # NaN → 0
    except Exception:
        return 0.0


def _evaluate(acao, entrada, stop, tp1, future):
    """Retorna (outcome, R). R = múltiplo de risco (ganho/perda em unidades de risco).
    WIN = +RR do TP1; LOSS = -1R; OPEN = marcado a mercado no último close."""
    risk  = abs(entrada - stop)
    rr_tp = abs(tp1 - entrada) / risk if risk else 0.0
    for _, row in future.iterrows():
        high, low = float(row["High"]), float(row["Low"])
        if acao == "COMPRA":
            if low <= stop:  return "LOSS", -1.0
            if high >= tp1:  return "WIN",  rr_tp
        else:
            if high >= stop: return "LOSS", -1.0
            if low <= tp1:   return "WIN",  rr_tp
    last_close = float(future.iloc[-1]["Close"])
    mtm = (last_close - entrada) if acao == "COMPRA" else (entrada - last_close)
    return "OPEN", (mtm / risk if risk else 0.0)


def run(symbol, tf_key, bars, forward, min_score, rr_override=None, adx_min=22, reverse=False, cost=0.0):
    print(f"\n📥  {symbol}  tf={tf_key}m  bars={bars}"
          + (f"  [alvo fixo {rr_override}R]" if rr_override else "")
          + ("  [REVERSE/fade]" if reverse else "")
          + (f"  [custo {cost}R/trade]" if cost else ""))
    df = _fetch(symbol, tf_key, bars)
    if df is None or len(df) < 80:
        return None
    htf = _fetch(symbol, "60", max(400, bars // 4))
    print(f"✅  {len(df)} candles ({df.index[0]} → {df.index[-1]})")

    results = []
    lookback = 60
    _CTX = 800   # janela DESLIZANTE: só as últimas ~800 velas (EMA200 já converge).
                 # Torna o loop O(n) em vez de O(n²) → roda em segundos, resultado
                 # praticamente idêntico (o peso de velas >800 atrás na EMA é ~0%).
    for i in range(lookback, len(df) - forward):
        window = df.iloc[max(0, i - _CTX):i]
        future = df.iloc[i:i + forward]
        htf_window = None
        if htf is not None:
            htf_window = htf[htf.index <= df.index[i]].tail(300)
            if len(htf_window) < 26:
                htf_window = None
        sig = generate_signal(window, htf_df=htf_window)
        if not sig or sig["acao"] not in ("COMPRA", "VENDA"):
            continue
        if abs(sig.get("score", 0)) < min_score:
            continue
        e, s, t = sig.get("entrada"), sig.get("stop"), sig.get("tp1")
        if None in (e, s, t):
            continue
        acao = sig["acao"]
        # --rr: sobrescreve SÓ o alvo por um múltiplo fixo do risco (mantém stop)
        if rr_override:
            risk = abs(e - s)
            t = (e + rr_override * risk) if acao == "COMPRA" else (e - rr_override * risk)
        # --reverse: FADE o sinal — inverte a direção e ESPELHA stop/tp em torno da
        # entrada (risco e alvo preservados). Testa se o ativo REVERTE em vez de seguir
        # tendência: se um ativo negativo vira positivo aqui, o sinal está certo, só
        # apontando pro lado errado (bastaria inverter a lógica dele).
        if reverse:
            risk = abs(e - s); tp_dist = abs(t - e)
            acao = "VENDA" if acao == "COMPRA" else "COMPRA"
            if acao == "COMPRA":
                s = e - risk; t = e + tp_dist
            else:
                s = e + risk; t = e - tp_dist
        outcome, R = _evaluate(acao, e, s, t, future)
        # CUSTO: desconta spread+slippage (em R) de TODO trade aberto. Com edge fino
        # e muitos trades (WDO), é o custo que decide se ganha ou perde de verdade.
        if cost:
            R -= cost
        # ── FILTRO anti-lateral + alinhamento macro ──────────────────
        adx = _adx(window.tail(120))
        htf_trend = (sig.get("htf_trend") or "").lower()
        if acao == "COMPRA":
            aligned = htf_trend in ("alta", "up", "bull", "compra")
        else:
            aligned = htf_trend in ("baixa", "down", "bear", "venda")
        results.append({
            "acao": acao, "score": sig["score"],
            "outcome": outcome, "R": R, "htf": sig.get("htf_trend"),
            "adx": round(adx, 1), "aligned": bool(aligned),
        })

    if not results:
        print("  (nenhum sinal com esses parâmetros)")
        return None
    r = pd.DataFrame(results)
    _report(symbol, r, min_score, adx_min)
    return {"symbol": symbol, "df": r, "adx_min": adx_min}


def _stats(r):
    """Métricas de PROFITABILIDADE (o que importa), não só win rate."""
    dec = r[r.outcome.isin(["WIN", "LOSS"])]
    wins = dec[dec.outcome == "WIN"]; losses = dec[dec.outcome == "LOSS"]
    n_dec = len(dec)
    wr = len(wins) / n_dec * 100 if n_dec else 0
    gross_win  = wins["R"].clip(lower=0).sum()
    gross_loss = -losses["R"].clip(upper=0).sum()  # positivo
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    # Expectância por trade DECIDIDO (em R) e incluindo OPEN marcado a mercado
    exp_dec = dec["R"].mean() if n_dec else 0
    exp_all = r["R"].mean() if len(r) else 0
    avg_win  = wins["R"].mean() if len(wins) else 0
    avg_loss = losses["R"].mean() if len(losses) else 0
    return {"n": len(r), "n_dec": n_dec, "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2), "exp_R_dec": round(exp_dec, 3),
            "exp_R_all": round(exp_all, 3), "avg_win_R": round(avg_win, 2),
            "avg_loss_R": round(avg_loss, 2),
            "total_R_dec": round(dec["R"].sum(), 1)}


def _line(label, st, opn=None):
    op = f"  OPEN {opn}" if opn is not None else ""
    print(f"  {label:16}: {st['n_dec']:>4} dec{op}  |  WR {st['win_rate']:>4.1f}%  "
          f"PF {st['profit_factor']:>5}  exp {st['exp_R_dec']:+.3f}R  soma {st['total_R_dec']:+.1f}R")


def _report(symbol, r, min_score, adx_min=22):
    st = _stats(r)
    opn = (r.outcome == "OPEN").sum()
    print(f"  Sinais: {st['n']}  |  decididos {st['n_dec']}  OPEN {opn}   [min_score={min_score}]")
    print(f"  WIN RATE       : {st['win_rate']:.1f}%")
    print(f"  PROFIT FACTOR  : {st['profit_factor']}   (>1 = lucrativo)")
    print(f"  EXPECTÂNCIA/tr : {st['exp_R_dec']:+.3f} R (decididos) | {st['exp_R_all']:+.3f} R (c/ open)")
    print(f"  R médio        : ganho {st['avg_win_R']:+.2f}R  perda {st['avg_loss_R']:+.2f}R  | soma {st['total_R_dec']:+.1f}R")
    veredito = "🟢 LUCRATIVO" if st["exp_R_dec"] > 0.03 else ("🟡 NEUTRO" if st["exp_R_dec"] > -0.03 else "🔴 NEGATIVO")
    print(f"  → {veredito} (expectância decidida {st['exp_R_dec']:+.3f} R/trade)")
    # Quebra por DIREÇÃO: revela se o ativo perde só num lado (ex.: só nas vendas)
    for lado in ("COMPRA", "VENDA"):
        d = r[r.acao == lado]
        dd = d[d.outcome.isin(["WIN", "LOSS"])]
        if len(dd) >= 20:
            print(f"     {lado:7}: {dd['R'].mean():+.3f} R  (n={len(dd)}, WR {(dd.outcome=='WIN').mean()*100:.0f}%, PF {_stats(d)['profit_factor']})")

    # ── COMPARAÇÃO: filtro anti-lateral (ADX) + alinhamento macro (HTF) ──
    if "adx" in r.columns and "aligned" in r.columns:
        print(f"\n  ── FILTRO anti-lateral (ADX≥{adx_min}) + alinhamento macro (HTF) ──")
        f_align = r[r.aligned]
        f_adx   = r[r.adx >= adx_min]
        f_both  = r[r.aligned & (r.adx >= adx_min)]
        _line("BASE (tudo)", st, opn)
        if len(f_align): _line("só alinhado HTF", _stats(f_align))
        if len(f_adx):   _line(f"só ADX≥{adx_min}", _stats(f_adx))
        if len(f_both):  _line("HTF + ADX", _stats(f_both))
        # varredura de ADX para achar o melhor corte
        print("  varredura ADX (com alinhamento HTF):")
        best = None
        for th in (15, 18, 20, 22, 25, 28, 30):
            sub = r[r.aligned & (r.adx >= th)]
            sd = sub[sub.outcome.isin(["WIN", "LOSS"])]
            if len(sd) >= 15:
                e = sd["R"].mean()
                print(f"     ADX≥{th:<3}: exp {e:+.3f}R  PF {_stats(sub)['profit_factor']:>5}  "
                      f"WR {(sd.outcome=='WIN').mean()*100:>3.0f}%  (n={len(sd)})")
                if best is None or e > best[1]:
                    best = (th, e)
        if best:
            print(f"     → melhor corte: ADX≥{best[0]}  (exp {best[1]:+.3f}R/trade)")

    # Expectância por faixa de score (o lever real, medido por R e não por acerto)
    print("  Expectância (R/trade) por faixa de |score|:")
    for s in range(min_score, 14):
        b = r[r.score.abs() >= s]
        bd = b[b.outcome.isin(["WIN", "LOSS"])]
        if len(bd) >= 10:
            print(f"     |score|≥{s:<3}: {bd['R'].mean():+.3f} R   (n={len(bd)}, WR {(bd.outcome=='WIN').mean()*100:.0f}%)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backtest do motor com dados do MT5")
    ap.add_argument("--symbol", default=None, help="Símbolo MT5 (ex: WINV26). Vazio = auto WIN+WDO")
    ap.add_argument("--tf", default="15", help="Timeframe: 1,5,15,30,60")
    ap.add_argument("--bars", type=int, default=6000, help="Qtd de candles a puxar")
    ap.add_argument("--forward", type=int, default=8, help="Velas à frente p/ checar TP/stop")
    ap.add_argument("--min-score", type=int, default=None, help="|score| mínimo (vazio = por ativo: WIN 9, WDO 7)")
    ap.add_argument("--rr", type=float, default=None, help="Testa alvo fixo em N×risco (ex: 1.5) em vez do TP1/S-R")
    ap.add_argument("--adx-min", type=int, default=22, help="Corte do filtro anti-lateral (ADX). Padrão 22")
    ap.add_argument("--reverse", action="store_true",
                    help="FADE: inverte a direção do sinal (testa se o ativo reverte em vez de seguir tendência)")
    ap.add_argument("--suite", action="store_true",
                    help="Roda a BATERIA toda num comando só: base, fade, e variações de alvo (RR)")
    ap.add_argument("--cost", type=float, default=0.0,
                    help="Custo por trade em R (spread+slippage). Ex.: 0.05 ou 0.1. Desconta do resultado.")
    ap.add_argument("--list", action="store_true", help="Lista símbolos WIN/WDO e sai")
    ap.add_argument("--depth", action="store_true",
                    help="Mostra QUANTO histórico existe por contrato (data inicial/final) e sai")
    args = ap.parse_args()

    if not _mt5_init():
        print(f"❌  MT5 não inicializou: {mt5.last_error()}")
        print("   Abra o MetaTrader 5, faça login, e confira MT5_* no .env")
        sys.exit(1)

    if args.list:
        for pref in ("WIN", "WDO"):
            syms = mt5.symbols_get(pref + "*") or []
            print(f"\n{pref}:")
            for s in syms[:20]:
                info = mt5.symbol_info(s.name)
                print(f"  {s.name:12} visible={getattr(info,'visible',False)}")
        mt5.shutdown(); sys.exit(0)

    # --depth: quanto histórico o broker serve por contrato (WIN e WDO expiram, então
    # cada contrato vive ~3 meses). Use isto p/ escolher quais contratos backtestar.
    if args.depth:
        print("═" * 66)
        print(f"  PROFUNDIDADE DE HISTÓRICO (tf={args.tf}m) — quanto o broker guarda")
        print("═" * 66)
        for pref in ("WIN", "WDO"):
            syms = mt5.symbols_get(pref + "*") or []
            print(f"\n{pref}:  (mostrando só contratos COM histórico)")
            achou = 0
            for s in sorted(syms, key=lambda x: x.name):
                d = _depth(s.name, args.tf)
                if d:
                    n, ini, fim = d
                    print(f"  {s.name:12} {n:>7} candles   {ini}  →  {fim}")
                    achou += 1
            if achou == 0:
                print("  (nenhum contrato retornou dados nesse timeframe)")
        print("\n  → Anote os contratos com histórico e rode, ex.:")
        print("     python backtest_mt5.py --symbol WING26,WINJ26,WINM26,WINQ26 --bars 20000")
        print("     (ele backtesta cada um e AGREGA num resultado só = período longo).\n")
        mt5.shutdown(); sys.exit(0)

    # --symbol aceita LISTA separada por vírgula (vários contratos → 1 ano agregado)
    if args.symbol:
        symbols = [s.strip() for s in args.symbol.split(",") if s.strip()]
    else:
        symbols = _resolve_symbols(None)
    print("═" * 56)
    print(f"  BACKTEST MT5 — motor v6  ·  símbolos: {', '.join(symbols)}")
    print("═" * 56)
    def _sym_min(sym):
        if args.min_score is not None:
            return args.min_score
        u = sym.upper()
        if u.startswith("WIN"): return 9   # v6.2: WIN só score alto
        if u.startswith("WDO"): return 7   # v6.2: WDO opera cedo
        return SCORE_MIN

    # --suite: bateria completa num comando só (demora mais, mas roda tudo de uma vez)
    if args.suite:
        battery = [
            ("BASE — segue tendência, saída TP1/S-R",   None, False),
            ("FADE — inverte a direção do sinal",        None, True),
            ("RR 1.0 — alvo = 1× risco (segue)",         1.0,  False),
            ("RR 2.0 — alvo = 2× risco (segue)",         2.0,  False),
            ("FADE + RR 1.0 — inverte, alvo = risco",    1.0,  True),
            ("FADE + RR 2.0 — inverte, alvo = 2× risco", 2.0,  True),
        ]
        for sym in symbols:
            for label, rr, rev in battery:
                print("\n" + "█" * 60)
                print(f"█  {sym}  ·  {label}")
                print("█" * 60)
                run(sym, args.tf, args.bars, args.forward, _sym_min(sym), rr, args.adx_min, rev, args.cost)
        print("\n" + "═" * 60)
        print("  Leitura: procure a linha com PROFIT FACTOR > 1 e expectância > 0.")
        print("  Se o FADE ficar positivo → o ativo reverte (invertemos a lógica).")
        print("  Se um RR mudar o jogo → o problema é a saída (ajustamos TP/SL).\n")
        mt5.shutdown(); sys.exit(0)

    summary = []
    fam_dfs = {}   # "WIN"/"WDO" -> lista de DataFrames de resultados (p/ agregar o período todo)
    for sym in symbols:
        out = run(sym, args.tf, args.bars, args.forward, _sym_min(sym), args.rr, args.adx_min, args.reverse, args.cost)
        if out:
            st = _stats(out["df"]); st["symbol"] = out["symbol"]
            summary.append(st)
            u = sym.upper()
            fam = "WIN" if u.startswith("WIN") else ("WDO" if u.startswith("WDO") else u[:3])
            fam_dfs.setdefault(fam, []).append(out["df"])
    if summary:
        print("\n" + "═" * 56)
        print("  RESUMO POR CONTRATO (a expectância é o que importa, não o win rate)")
        for st in summary:
            flag = "🟢" if st["exp_R_dec"] > 0.03 else ("🟡" if st["exp_R_dec"] > -0.03 else "🔴")
            print(f"  {flag} {st['symbol']:10} → exp {st['exp_R_dec']:+.3f} R/tr  "
                  f"PF {st['profit_factor']}  WR {st['win_rate']:.0f}%  ({st['n_dec']} dec)")
        # ── AGREGADO do período todo por ativo (todos os contratos WIN juntos, etc.) ──
        if any(len(v) > 1 for v in fam_dfs.values()):
            print("\n  ── PERÍODO TODO AGREGADO (todos os contratos do ativo somados) ──")
            for fam, dfs in fam_dfs.items():
                allr = pd.concat(dfs, ignore_index=True)
                c = _stats(allr)
                flag = "🟢" if c["exp_R_dec"] > 0.03 else ("🟡" if c["exp_R_dec"] > -0.03 else "🔴")
                print(f"  {flag} {fam:6} TODO → {c['n_dec']} trades  exp {c['exp_R_dec']:+.3f} R/tr  "
                      f"PF {c['profit_factor']}  WR {c['win_rate']:.0f}%  soma {c['total_R_dec']:+.1f}R")
                # expectância por faixa de |score| no agregado (mostra onde ajustar o threshold)
                for s in range(4, 13):
                    b = allr[allr.score.abs() >= s]
                    bd = b[b.outcome.isin(["WIN", "LOSS"])]
                    if len(bd) >= 20:
                        print(f"       |score|≥{s:<3}: {bd['R'].mean():+.3f} R  (n={len(bd)}, WR {(bd.outcome=='WIN').mean()*100:.0f}%)")
        print("═" * 56)
        print("\n  Interpretação: profit factor > 1 e expectância > 0 = sistema ganha.")
        print("  Rode com --min-score 4 para ver a expectância por faixa de score")
        print("  (a tabela por |score| mostra se subir o threshold ajuda no R).\n")
    mt5.shutdown()
