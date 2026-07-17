# Rodar B3 e Crypto ao mesmo tempo (duas instâncias)

## Por que duas instâncias?
A biblioteca **MetaTrader5 do Python é UMA conexão por processo**. Não dá pra falar
com dois terminais ao mesmo tempo dentro do mesmo app — por isso, quando tudo rodava
junto, uma conta trocava a outra. A solução é subir **dois processos**: um fixado no
terminal da XP (B3) e outro no da ICMarkets (crypto), cada um na sua porta.

## Passo 1 — Ajustar o `.env`
Deixe as credenciais da **B3 (XP)** nas variáveis normais e adicione um bloco novo
para **crypto (ICMarkets)** com o prefixo `MT5_CRYPTO_`:

```ini
# ---- B3 / XP (usado pela instância b3) ----
MT5_LOGIN=<seu login XP/B3>
MT5_PASSWORD=<senha XP>
MT5_SERVER=<servidor XP>
MT5_PATH=C:\Program Files\XP...\terminal64.exe

# ---- CRYPTO / ICMarkets (usado pela instância crypto) ----
MT5_CRYPTO_LOGIN=52956012
MT5_CRYPTO_PASSWORD=<senha ICMarkets>
MT5_CRYPTO_SERVER=ICMarketsSC-Demo
MT5_CRYPTO_PATH=C:\Program Files\ICMarkets...\terminal64.exe
```

> ⚠️ **O `MT5_PATH` e o `MT5_CRYPTO_PATH` são obrigatórios e têm que apontar para os
> dois `terminal64.exe` DIFERENTES** (instalações separadas). É isso que faz cada
> processo se conectar ao terminal certo. Se ficarem vazios ou iguais, os processos
> podem grudar no mesmo terminal.

Para achar o caminho: no MT5 → **Arquivo → Abrir Pasta de Dados** não serve; use a
pasta de instalação (onde está o `terminal64.exe`). Ou clique com o botão direito no
atalho do MT5 → Propriedades → "Destino".

## Passo 2 — Abrir os dois terminais
Abra o **MT5 da XP** (logado na conta B3) e o **MT5 da ICMarkets** (logado na
52956012). Deixe os dois abertos.

## Passo 3 — Iniciar as duas instâncias
- Duplo clique em **`iniciar_B3.bat`** → sobe a instância B3 na porta **5000**.
- Duplo clique em **`iniciar_CRYPTO.bat`** → sobe a instância Crypto na porta **5001**.

## Passo 4 — Duas abas no navegador
- **B3:** http://localhost:5000/newdashboard → menus Monitor MT5 / Scalper
- **Crypto:** http://localhost:5001/newdashboard → menu Monitor Crypto

Cada aba usa a instância que fala com o terminal certo. **Não use o menu Monitor MT5
na aba do Crypto (5001)** — essa instância está conectada à ICMarkets, então dados de
B3 ali ficariam errados (e vice-versa).

## Como funciona por dentro (sem tocar nos motores)
No boot, se `MT5_PROFILE=crypto` (setado pelo `iniciar_CRYPTO.bat`), o app copia
`MT5_CRYPTO_*` por cima de `MT5_*` — então **todo** esse processo passa a usar a conta
de crypto. A instância B3 (`MT5_PROFILE=b3`, padrão) segue usando `MT5_*` como sempre.
Os agendadores de B3/Scalper (Telegram, relatório diário, supervisor) só rodam na
instância B3. Nenhuma lógica dos motores foi alterada.
