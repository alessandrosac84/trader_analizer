import MetaTrader5 as mt5
import pandas as pd

if not mt5.initialize():
    print("ERRO ao conectar:", mt5.last_error())
else:
    print("✅ MT5 conectado!")
    print("Versão:", mt5.version())
    
    # Busca 10 candles M15 do WINM26
    rates = mt5.copy_rates_from_pos("WINM26", mt5.TIMEFRAME_M15, 0, 10)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(df[["time","open","high","low","close","tick_volume"]])
    
    mt5.shutdown()