from pathlib import Path

# --- crypto_config ---
p = Path("services/crypto_config.py")
t = p.read_text(encoding="utf-8")
old = """        if sym in EUR_PDH_H4_SYMBOLS:
            out.append(\"EUR_PDH_H4\")
    except Exception:
        pass
    # Score / impulso / pullback só se o ativo NÃO estiver vetado"""
new = """        if sym in EUR_PDH_H4_SYMBOLS:
            out.append(\"EUR_PDH_H4\")
    except Exception:
        pass
    try:
        from services.crypto_discovery_paths import discovery_path_names
        out.extend(discovery_path_names(sym))
    except Exception:
        pass
    # Score / impulso / pullback só se o ativo NÃO estiver vetado"""
if old not in t:
    raise SystemExit("anchor not found in crypto_config")
p.write_text(t.replace(old, new, 1), encoding="utf-8")
print("crypto_config OK")

# --- crypto_bp: insert discovery cascade before auto_blocked early return ---
bp = Path("blueprints/crypto_bp.py")
bt = bp.read_text(encoding="utf-8")
anchor = """        # Ativo com score legado OFF (ex.: BTC/ETH): só caminhos GO acima.
        # Evita cair em score/pullback e ficar spitando \"NEUTRO\" como se fosse gatilho.
        if auto_blocked(symbol):"""
block = '''        # ── Discovery feature-condition GOs (harness 26/07 · JSON allowlist) ──
        # Depois dos GOs clássicos; ordem = ranking net no JSON. ETH_S_36 usa M60.
        try:
            from services.crypto_discovery_paths import discovery_enabled, paths_by_tf, eval_path
            if discovery_enabled(symbol):
                _by_tf = paths_by_tf(symbol)
                _tf_cache = {15: (_m15_all, _m15_err)}
                for _tf, _plist in sorted(_by_tf.items()):
                    if _tf not in _tf_cache:
                        _tf_cache[_tf] = get_candles(symbol, int(_tf), 2000)
                    _c_all, _c_err = _tf_cache[_tf]
                    if _c_err or not _c_all:
                        continue
                    _c = _c_all if len(_c_all) <= 2000 else _c_all[-2000:]
                    for _p in _plist:
                        _sig = eval_path(_c, _p)
                        if not _sig:
                            continue
                        _tag = _p.get("path") or _p.get("name")
                        _mot = _p.get("comment") or f"[{_tag} GO disc] feature-cond SL1 TP2"
                        _resp = _fire_tuple_go(_tag, _sig, _mot)
                        if _resp is not None:
                            return _resp
        except Exception:
            pass

        # Ativo com score legado OFF (ex.: BTC/ETH): só caminhos GO acima.
        # Evita cair em score/pullback e ficar spitando \"NEUTRO\" como se fosse gatilho.
        if auto_blocked(symbol):'''
if anchor not in bt:
    raise SystemExit("anchor not found in crypto_bp")
bp.write_text(bt.replace(anchor, block, 1), encoding="utf-8")
print("crypto_bp OK")
