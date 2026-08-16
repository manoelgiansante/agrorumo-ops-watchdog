# agrorumo-ops-watchdog

Vigia **externo** do Mac Mini da AgroRumo.

## Por que existe

O `fleet-deadman.sh` roda **dentro** do Mac Mini. Ele mesmo diz, na linha 3:

> `NAO pega Mini fisicamente desligado (precisa monitor externo)`

O único monitor externo era um `launchd` no **MacBook** — que viaja com o CEO e pode
ficar dias desligado. Resultado: se o Mini caísse (energia, internet, kernel panic,
logout), **ninguém avisava**. Este repositório fecha esse buraco: quem julga roda na
infra do GitHub, independente das duas máquinas.

## Como funciona

1. O Mini publica um JSON de saúde num **gist secreto** a cada 10 min
   (`~/scripts/agrorumo/mini-heartbeat-push.sh`, launchd `com.agrorumo.mini-heartbeat-push`).
2. O workflow `mini-deadman` roda a cada 15 min, lê o gist e julga:
   - **MORTO** — pulso mais velho que 45 min
   - **DOENTE** — pulso fresco, mas disco/swap/memória/jobs no vermelho
   - **VIVO** — resto
3. Alerta o CEO por **Telegram** (canal principal) e, só quando MORTO, também por
   **WhatsApp** (template UTILITY `ops_alert_ceo`).
4. `state/state.json` guarda o estado para não repetir alerta:
   máx. 1 alerta/6 h; WhatsApp máx. 1/12 h (ZERO-AF: sem rajada). Manda 1 aviso de
   recuperação quando volta.

O payload do gist **não contém segredo** — só métrica de saúde.

## Testar sem incomodar o CEO

Actions → `mini-deadman` → *Run workflow* → `force_stale: true`, `dry_run: true`.
O log mostra o veredito e a mensagem exata que seria enviada, sem enviar nada.

Para um teste **real** (chega no celular): mesma coisa com `dry_run: false`.

## Desligar

Desabilitar o workflow em Actions, ou no Mini:

```
launchctl bootout gui/$(id -u)/com.agrorumo.mini-heartbeat-push
```
