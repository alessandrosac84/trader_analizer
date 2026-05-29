# diagnostico_mt5.py
# Rode este script SEPARADAMENTE no terminal Windows (cmd ou PowerShell)
# para diagnosticar o problema de conexao com o MT5.
#
# Como rodar:
#   cd /d "C:/Users/aless/OneDrive/Documentos/GIT_PESSOAL/trader_analizer"
#   python diagnostico_mt5.py
import sys
import os
import struct
import glob
import subprocess

print("=" * 60)
print("DIAGNOSTICO MT5 v2")
print("=" * 60)

# ── 1. Arquitetura do Python ───────────────────────────────────
bits = struct.calcsize("P") * 8
print(f"\n[PYTHON] Versao: {sys.version.split()[0]}")
print(f"[PYTHON] Arquitetura: {bits}-bit")
if bits != 64:
    print("[ERRO CRITICO] Python precisa ser 64-bit para o MT5.")
    print("               Baixe Python 64-bit em https://python.org")
    sys.exit(1)
else:
    print("[OK] Python 64-bit -- compativel com MT5")

# ── 2. MT5 processo esta rodando? ──────────────────────────────
print("\n--- Verificando processo MT5 no Windows ---")
try:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV"],
        capture_output=True, text=True, timeout=5
    )
    if "terminal64.exe" in result.stdout:
        print("[OK] Processo terminal64.exe ENCONTRADO rodando:")
        for linha in result.stdout.strip().split("\n"):
            if "terminal64" in linha.lower():
                print(f"     {linha.strip()}")
    else:
        print("[AVISO] Processo terminal64.exe NAO esta rodando!")
        print("        Abra o MT5 e logue antes de rodar este script.")
except Exception as e:
    print(f"[INFO] Nao foi possivel checar processos: {e}")

# ── 3. Encontrar instalacao do MT5 ─────────────────────────────
print("\n--- Buscando instalacao do MT5 ---")
padroes = [
    r"C:\Program Files\XP Investimentos MT5\terminal64.exe",
    r"C:\Program Files\XP Investimentos\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs", "MetaTrader 5", "terminal64.exe"),
]
# Busca em AppData/Roaming/MetaQuotes (instalacoes portaveis)
appdata_roaming = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "MetaQuotes", "Terminal")
if os.path.isdir(appdata_roaming):
    for pasta in os.listdir(appdata_roaming):
        exe = os.path.join(appdata_roaming, pasta, "terminal64.exe")
        if os.path.exists(exe):
            padroes.append(exe)

terminais_encontrados = []
for padrao in padroes:
    if "*" in padrao:
        for m in glob.glob(padrao):
            if m not in terminais_encontrados:
                print(f"  [ENCONTRADO] {m}")
                terminais_encontrados.append(m)
    elif os.path.exists(padrao):
        if padrao not in terminais_encontrados:
            print(f"  [ENCONTRADO] {padrao}")
            terminais_encontrados.append(padrao)

if not terminais_encontrados:
    print("  [AVISO] Nenhum terminal64.exe encontrado nos locais padrao.")
    print("  Dica: Abra o MT5, va em Ajuda > Sobre e veja o caminho.")

# ── 4. Verifica biblioteca MetaTrader5 ─────────────────────────
print("\n--- MetaTrader5 Python ---")
try:
    import MetaTrader5 as mt5
    print(f"[OK] MetaTrader5 instalado -- versao: {mt5.__version__}")
except ImportError:
    print("[ERRO] MetaTrader5 NAO instalado. Rode: pip install MetaTrader5")
    sys.exit(1)

# ── 5. mt5.version() sem initialize() ─────────────────────────
ver = mt5.version()
print(f"[INFO] mt5.version() sem initialize = {ver}")
if ver == (0, 0, '') or ver is None:
    print("[AVISO] Retornou (0,0,'') -- biblioteca NAO localiza o terminal.")
    print("        Causa mais comum: MT5 fechado OU privilegios diferentes.")
else:
    print(f"[OK] Terminal MT5 respondeu: versao {ver}")

# ── 6. Tentativa 1: initialize() simples ──────────────────────
print("\n--- Tentativa 1: initialize() simples ---")
ok = mt5.initialize()
print(f"Resultado: {ok}")
if not ok:
    print(f"Erro: {mt5.last_error()}")
    mt5.shutdown()
else:
    acc = mt5.account_info()
    print(f"[SUCESSO] Conta: {acc.login} | {acc.name} | {acc.server}")
    print(f"          Saldo: {acc.balance} | {'DEMO' if acc.trade_mode == 0 else 'REAL'}")
    mt5.shutdown()
    print("\n[TUDO OK] MT5 conecta! Reinicie o Flask e teste.")
    sys.exit(0)

# ── 7. Tentativa 2: com cada caminho encontrado ─────────────────
if terminais_encontrados:
    print("\n--- Tentativa 2: com caminho do terminal ---")
    for caminho in terminais_encontrados:
        print(f"Testando: {caminho}")
        ok = mt5.initialize(path=caminho)
        if ok:
            acc = mt5.account_info()
            print(f"[SUCESSO] Conectado! Conta: {acc.login} | {acc.server}")
            print(f"\nADICIONE no .env:")
            print(f"MT5_PATH={caminho}")
            mt5.shutdown()
            sys.exit(0)
        else:
            print(f"  Falhou: {mt5.last_error()}")
            mt5.shutdown()

# ── 8. Tentativa 3: com credenciais do .env ────────────────────
print("\n--- Tentativa 3: com credenciais do .env ---")
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

login    = int(os.getenv("MT5_LOGIN", "0") or 0)
password = os.getenv("MT5_PASSWORD", "")
server   = os.getenv("MT5_SERVER", "")
path     = os.getenv("MT5_PATH", "")

if login and password and server:
    print(f"Tentando login={login} server={server}")
    kwargs = {"login": login, "password": password, "server": server}
    if path and os.path.exists(path):
        kwargs["path"] = path
    elif terminais_encontrados:
        kwargs["path"] = terminais_encontrados[0]
    ok = mt5.initialize(**kwargs)
    if ok:
        acc = mt5.account_info()
        print(f"[SUCESSO] Conectado com credenciais! Conta: {acc.login}")
        mt5.shutdown()
        sys.exit(0)
    else:
        print(f"[ERRO] {mt5.last_error()}")
        mt5.shutdown()
else:
    print("[AVISO] MT5_LOGIN/MT5_PASSWORD/MT5_SERVER nao estao no .env")
    print("        Para testar com a conta demo, preencha no .env:")
    print("          MT5_LOGIN=59234894")
    print("          MT5_PASSWORD=sua_senha_aqui")
    print("          MT5_SERVER=XPMT5-Demo")
    print("        (o servidor exato aparece na janela de login do MT5)")

# ── 9. Diagnostico final ───────────────────────────────────────
print("\n" + "=" * 60)
print("NENHUMA TENTATIVA FUNCIONOU. Checklist:")
print()
print("[ ] 1. O MT5 esta aberto e LOGADO na conta?")
print("[ ] 2. Ferramentas > Opcoes > Expert Advisors")
print("       Marque: 'Permitir negociacao automatizada'")
print("       Marque: 'Permitir importacoes de DLL'")
print("       Clique OK e REINICIE o MT5 completamente")
print("[ ] 3. Mesmo privilegio para MT5 e CMD:")
print("       -> Clique direito no MT5  > 'Executar como administrador'")
print("       -> Clique direito no CMD  > 'Executar como administrador'")
print("       -> Rode novamente: python diagnostico_mt5.py")
print("[ ] 4. Preencha credenciais no .env e rode novamente")
print("=" * 60)
