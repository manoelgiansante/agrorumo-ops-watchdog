#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIGIA EXTERNO do Mac Mini da AgroRumo.  (FRENTE 6 / viagem 2026-08-16)

POR QUE EXISTE
--------------
O fleet-deadman.sh roda DENTRO do Mini: ele nao consegue avisar a propria morte
(o proprio arquivo diz "NAO pega Mini fisicamente desligado"). O unico monitor
externo era um launchd no MacBook — que viaja com o CEO e pode ficar desligado.
Este script roda no GitHub Actions: um TERCEIRO independente das duas maquinas.

COMO FUNCIONA
-------------
1. O Mini publica um JSON de saude num gist secreto a cada 10 min.
2. Este script le o gist e julga:
      MORTO  = pulso mais velho que DEAD_AFTER_MIN (default 45 min)
      DOENTE = pulso fresco, mas metrica no vermelho (disco/swap/memoria/jobs)
      VIVO   = resto
3. Alerta o CEO por Telegram (canal principal, provado) e, so no caso MORTO,
   tambem por WhatsApp (template UTILITY ops_alert_ceo).
4. Guarda estado em state/state.json (commitado) para nao repetir alerta:
   MORTO  -> no maximo 1 alerta a cada ALERT_EVERY_H  (default 6h)
   WhatsApp-> no maximo 1 a cada WA_EVERY_H (default 12h; ZERO-AF, sem rajada)
   VOLTOU -> 1 unico aviso de recuperacao.

NUNCA imprime segredo: tokens vem de secrets do Actions e nao sao ecoados.
"""
import json, os, sys, time, urllib.request, urllib.parse, urllib.error

GIST_ID       = os.environ.get("GIST_ID", "").strip()
DEAD_AFTER    = int(os.environ.get("DEAD_AFTER_MIN", "45")) * 60
ALERT_EVERY   = int(os.environ.get("ALERT_EVERY_H", "6")) * 3600
WA_EVERY      = int(os.environ.get("WA_EVERY_H", "12")) * 3600
FORCE_STALE   = os.environ.get("FORCE_STALE", "false").lower() == "true"
DRY_RUN       = os.environ.get("DRY_RUN", "false").lower() == "true"
STATE_PATH    = "state/state.json"

TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT    = os.environ.get("TG_CHAT_ID", "")
WA_TOKEN   = os.environ.get("WA_TOKEN", "")
WA_PHONE   = os.environ.get("WA_PHONE_ID", "")
WA_TPL     = os.environ.get("WA_TEMPLATE", "ops_alert_ceo")
WA_LANG    = os.environ.get("WA_LANG", "pt_BR")
CEO_PHONE  = os.environ.get("CEO_PHONE", "")

# limiares de saude (o pulso esta fresco, mas a maquina esta indo pro brejo)
LIM_DISK_FREE  = int(os.environ.get("LIM_DISK_FREE", "8"))    # ZERO-AG: <10% e fator de colapso
LIM_SWAP_USED  = int(os.environ.get("LIM_SWAP_USED", "97"))
LIM_MEM_FREE   = int(os.environ.get("LIM_MEM_FREE", "8"))
LIM_JOBS_MIN   = int(os.environ.get("LIM_JOBS_MIN", "40"))    # frota normal ~83


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "agrorumo-ops-watchdog",
        "Cache-Control": "no-cache",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def read_beat():
    """RAW primeiro (sem rate limit, cache 5min). Se parecer velho, confirma pela
    API (cache 60s) — assim uma leitura cacheada nunca vira alarme falso."""
    raw_url = ("https://gist.githubusercontent.com/manoelgiansante/"
               f"{GIST_ID}/raw/mini-heartbeat.json?cb={int(time.time())}")
    beat, src = None, "?"
    try:
        beat, src = json.loads(get(raw_url)), "raw"
    except Exception as e:
        print(f"[warn] raw falhou: {type(e).__name__}")
    if beat is None or (time.time() - beat.get("ts", 0)) > DEAD_AFTER * 0.6:
        try:
            d = json.loads(get(f"https://api.github.com/gists/{GIST_ID}"))
            beat = json.loads(d["files"]["mini-heartbeat.json"]["content"])
            src = "api"
        except Exception as e:
            print(f"[warn] api falhou: {type(e).__name__}")
    return beat, src


def load_state():
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {"status": "unknown", "last_alert_ts": 0, "last_wa_ts": 0}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    json.dump(st, open(STATE_PATH, "w"), indent=1, sort_keys=True)


def send_telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        print("[skip] telegram: secret ausente"); return False
    try:
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data)
        r = json.loads(urllib.request.urlopen(req, timeout=25).read().decode())
        mid = r.get("result", {}).get("message_id")
        print(f"[ok] telegram entregue (message_id={mid})")   # id = prova (D1)
        return bool(r.get("ok"))
    except Exception as e:
        print(f"[FALHA] telegram: {type(e).__name__}"); return False


def send_whatsapp(text):
    if not (WA_TOKEN and WA_PHONE and CEO_PHONE):
        print("[skip] whatsapp: secret ausente"); return False
    flat = " ".join(text.split())[:900]
    body = json.dumps({
        "messaging_product": "whatsapp",
        "to": "".join(c for c in CEO_PHONE if c.isdigit()),
        "type": "template",
        "template": {"name": WA_TPL, "language": {"code": WA_LANG},
                     "components": [{"type": "body",
                                     "parameters": [{"type": "text", "text": flat}]}]},
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://graph.facebook.com/v22.0/{WA_PHONE}/messages", data=body,
            headers={"Authorization": f"Bearer {WA_TOKEN}",
                     "Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=25).read().decode())
        mid = (r.get("messages") or [{}])[0].get("id")
        print(f"[ok] whatsapp aceito (id={'sim' if mid else 'nao'})")
        return bool(mid)
    except urllib.error.HTTPError as e:
        print(f"[FALHA] whatsapp HTTP {e.code}")   # corpo omitido: pode ecoar dado
        return False
    except Exception as e:
        print(f"[FALHA] whatsapp: {type(e).__name__}"); return False


def main():
    if not GIST_ID:
        print("GIST_ID ausente"); return 1

    beat, src = read_beat()
    now = int(time.time())
    st = load_state()

    if beat is None:
        age, verdict, motivos = -1, "MORTO", ["nao consegui ler o pulso (gist inacessivel)"]
    else:
        age = now - int(beat.get("ts", 0))
        if FORCE_STALE:
            age = DEAD_AFTER + 3600
            print("[TESTE] FORCE_STALE ligado: tratando o pulso como velho de proposito")
        motivos = []
        if age > DEAD_AFTER:
            verdict = "MORTO"
            motivos.append(f"pulso parado ha {age//60} min (normal <10 min)")
        else:
            verdict = "VIVO"
            g = lambda k: int(beat.get(k, -1))
            if 0 <= g("disk_free_pct") < LIM_DISK_FREE:
                motivos.append(f"disco livre {g('disk_free_pct')}% (<{LIM_DISK_FREE}%)")
            if g("swap_used_pct") > LIM_SWAP_USED:
                motivos.append(f"swap {g('swap_used_pct')}% (>{LIM_SWAP_USED}%)")
            if 0 <= g("mem_free_pct") < LIM_MEM_FREE:
                motivos.append(f"memoria livre {g('mem_free_pct')}% (<{LIM_MEM_FREE}%)")
            if 0 <= g("jobs_loaded") < LIM_JOBS_MIN:
                motivos.append(f"so {g('jobs_loaded')} jobs carregados (normal ~83)")
            if motivos:
                verdict = "DOENTE"

    print(f"fonte={src} idade_do_pulso={age}s veredito={verdict} motivos={motivos}")
    if beat:
        print("pulso:", json.dumps(beat, ensure_ascii=False))

    prev = st.get("status", "unknown")
    alerta_devido = (now - st.get("last_alert_ts", 0)) >= ALERT_EVERY
    wa_devido     = (now - st.get("last_wa_ts", 0)) >= WA_EVERY
    acoes = []

    if verdict in ("MORTO", "DOENTE"):
        primeira_vez = (prev != verdict)
        if primeira_vez or alerta_devido:
            icone = "🔴" if verdict == "MORTO" else "🟠"
            # FORCE_STALE so existe em teste manual. Marcar a mensagem evita que um
            # teste seja lido como queda real (nao gritar "lobo" no celular do CEO).
            selo = "🧪 TESTE DO ALARME (disparado de proposito, o Mini esta bem) — " if FORCE_STALE else ""
            txt = (f"{selo}{icone} MAC MINI {verdict} — vigia externo (GitHub, fora das 2 maquinas)\n"
                   f"Motivo: {'; '.join(motivos)}\n"
                   f"Ultimo pulso: {beat.get('iso','?') if beat else 'nenhum'}\n"
                   f"Painel: https://comitiva.agrorumo.com\n"
                   f"Se estiver MORTO: a frota, o Bob e os briefings pararam. "
                   f"Peca a alguem para religar o Mac Mini na tomada/energia.")
            if DRY_RUN:
                print("[DRY-RUN] mensagem que SERIA enviada:\n" + txt); acoes.append("dry-run")
            else:
                if send_telegram(txt):
                    acoes.append("telegram")
                if verdict == "MORTO" and wa_devido and send_whatsapp(txt):
                    acoes.append("whatsapp"); st["last_wa_ts"] = now
                st["last_alert_ts"] = now
        else:
            print(f"[throttle] ja alertei ha {(now - st.get('last_alert_ts',0))//60} min "
                  f"(limite {ALERT_EVERY//3600}h) — nao repito")
    elif verdict == "VIVO" and prev in ("MORTO", "DOENTE"):
        txt = (f"🟢 MAC MINI VOLTOU — vigia externo.\n"
               f"Pulso normal de novo ({age//60} min atras).\n"
               f"Painel: https://comitiva.agrorumo.com")
        if DRY_RUN:
            print("[DRY-RUN] recuperacao:\n" + txt); acoes.append("dry-run")
        else:
            send_telegram(txt); acoes.append("telegram-recuperacao")

    if not DRY_RUN:
        st["status"] = verdict
        st["last_check_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        st["last_beat_age_s"] = age
        save_state(st)

    print(f"acoes={acoes or ['nenhuma']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
