"""edge_discovery/config.py — configuração central do Edge Discovery Engine."""

# Ativos por grupo (grupo = qual MT5 precisa estar aberto)
ASSETS = {
    "b3":     ["WIN$D", "WDO$D", "BITN26", "VALE3", "PETR4", "ITUB4", "ABEV3"],
    "crypto": ["BTCUSD", "ETHUSD", "XAUUSD", "EURUSD", "GBPUSD"],
}

TIMEFRAMES = [5, 15, 30, 60, 240]        # M5, M15, M30, H1, H4
BARS_MAX   = 200000                       # pede o máximo; o broker limita

# Labels (alvos em R, risco = 1×ATR; e novas máximas/mínimas)
TARGETS_R   = [1.0, 2.0, 3.0]
LOOKAHEAD   = 60                          # barras máximas p/ resolver o label
NEWHILO_NS  = [10, 20, 40]

# Descoberta
MIN_SUPPORT_UNI = 300                     # n mínimo p/ padrão univariado
MIN_SUPPORT_BI  = 150                     # n mínimo p/ combinação de 2 features
MIN_LIFT        = 1.15                    # lift mínimo in-sample
MIN_Z           = 3.0                     # significância (z binomial)
TOP_FEATURES_BI = 15                      # nº de features cruzadas 2 a 2
OOS_FRAC        = 0.30                    # out-of-sample temporal
MIN_LIFT_OOS    = 1.08                    # o padrão precisa segurar no OOS

# Sessões (hora do índice do candle; B3 = local, crypto = servidor IC ~GMT+2)
SESSIONS_24H = {"asia": (1, 9), "london": (9, 16), "ny": (16, 23)}
