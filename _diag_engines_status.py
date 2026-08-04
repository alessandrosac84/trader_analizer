import sys, traceback
sys.path.insert(0, r"C:\Projetos\trader_analizer")
print("importing services.b3_trade_hub...", flush=True)
try:
    from services.b3_trade_hub import engines_status
    print("import_ok", flush=True)
    print("calling engines_status()...", flush=True)
    r = engines_status()
    s = repr(r)
    print("RESULT_TYPE=", type(r).__name__, flush=True)
    print("RESULT_FIRST_500=", s[:500], flush=True)
except Exception:
    traceback.print_exc()
    sys.exit(1)
