"""
Motor de pronósticos de fútbol (API-Football).

Uso:
    pip install requests
    export API_FOOTBALL_KEY="tu_clave"        # Windows: set API_FOOTBALL_KEY=tu_clave
    python motor_futbol.py                    # analiza los partidos de hoy
    python motor_futbol.py 2026-09-25         # analiza otra fecha
    python motor_futbol.py ligas Colombia     # busca los IDs de ligas de un país

Genera resultados.json (para la app) e imprime un resumen en consola.
Las respuestas se guardan en cache.json para gastar pocas consultas de la API.
"""
import json
import math
import os
import sys
import time
from datetime import date

import requests

KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE = "https://v3.football.api-sports.io"
SEASON = int(os.environ.get("SEASON", 2026))   # año en que empieza la temporada
N_PARTIDOS = 6                                  # últimos partidos por equipo
ZONA = "America/Bogota"

# IDs de API-Football. Verifícalos con: python motor_futbol.py ligas <País>
LIGAS = {
    140: "LaLiga", 143: "Copa del Rey",
    39: "Premier League", 45: "FA Cup", 48: "Carabao Cup",
    135: "Serie A", 137: "Coppa Italia",
    61: "Ligue 1", 66: "Coupe de France",
    94: "Primeira Liga", 96: "Taça de Portugal",
    239: "Liga BetPlay", 241: "Copa Colombia",
    78: "Bundesliga", 81: "DFB Pokal",
    2: "Champions League", 3: "Europa League",
}

CACHE_FILE = "cache.json"
cache = json.load(open(CACHE_FILE)) if os.path.exists(CACHE_FILE) else {}


def api(path, ttl=6 * 3600, **params):
    """Llama a la API con cache en disco. ttl = segundos que vale el cache."""
    k = path + json.dumps(params, sort_keys=True)
    hit = cache.get(k)
    if hit and time.time() - hit["t"] < ttl:
        return hit["d"]
    r = requests.get(BASE + path, headers={"x-apisports-key": KEY},
                     params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("errors"):
        print("Aviso de la API:", j["errors"])
        return []
    cache[k] = {"t": time.time(), "d": j["response"]}
    return j["response"]


def promedio(lst):
    return sum(lst) / len(lst) if lst else None


def corners_partido(fixture_id, team_id):
    """Devuelve (córners a favor, córners en contra) de un equipo en un partido."""
    est = api("/fixtures/statistics", ttl=30 * 86400, fixture=fixture_id)
    val = {}
    for e in est:
        for s in e["statistics"]:
            if s["type"] == "Corner Kicks":
                val[e["team"]["id"]] = s["value"] or 0
    if team_id in val and len(val) == 2:
        return val[team_id], sum(val.values()) - val[team_id]
    return None


def historial(team_id):
    """Goles, forma y córners (local/visitante) en los últimos N partidos."""
    fx = api("/fixtures", ttl=12 * 3600, team=team_id, last=N_PARTIDOS)
    gf, gc, forma = [], [], ""
    cor = {"casa": ([], []), "fuera": ([], [])}   # (a favor, en contra)
    for f in fx:
        casa = f["teams"]["home"]["id"] == team_id
        g = f["goals"]
        mis = (g["home"] if casa else g["away"]) or 0
        rival = (g["away"] if casa else g["home"]) or 0
        gf.append(mis)
        gc.append(rival)
        forma += "G" if mis > rival else "E" if mis == rival else "P"
        c = corners_partido(f["fixture"]["id"], team_id)
        if c:
            lado = cor["casa" if casa else "fuera"]
            lado[0].append(c[0])
            lado[1].append(c[1])
    return {
        "forma": forma,
        "ganados": forma.count("G"), "empatados": forma.count("E"),
        "perdidos": forma.count("P"),
        "goles_favor": promedio(gf), "goles_contra": promedio(gc),
        "corners_local": {"favor": promedio(cor["casa"][0]),
                          "contra": promedio(cor["casa"][1])},
        "corners_visitante": {"favor": promedio(cor["fuera"][0]),
                              "contra": promedio(cor["fuera"][1])},
    }


def tiros_por_jugador(team_id):
    """Top 3 jugadores por promedio de tiros al arco por partido."""
    res = api("/players", ttl=24 * 3600, team=team_id, season=SEASON)
    lst = []
    for p in res:
        # la API escribe "appearences" (con esa ortografía)
        pj = sum((s["games"]["appearences"] or 0) for s in p["statistics"])
        ta = sum((s["shots"]["on"] or 0) for s in p["statistics"])
        if pj >= 3:
            lst.append({"jugador": p["player"]["name"], "tiros_arco_pj": round(ta / pj, 2)})
    return sorted(lst, key=lambda x: -x["tiros_arco_pj"])[:3]


def poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def pronostico(h, a):
    """Probabilidades 1X2 y más de 2.5 goles con el modelo de Poisson."""
    if h["goles_favor"] is None or a["goles_favor"] is None:
        return None
    lh = (h["goles_favor"] + a["goles_contra"]) / 2
    la = (a["goles_favor"] + h["goles_contra"]) / 2
    ph = pd = pa = po = 0.0
    for i in range(9):
        for j in range(9):
            p = poisson(i, lh) * poisson(j, la)
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
            if i + j > 2:
                po += p
    t = ph + pd + pa
    ph, pd, pa = ph / t, pd / t, pa / t
    mejor = max((ph, "Gana local"), (pd, "Empate"), (pa, "Gana visitante"))[1]
    return {"goles_proyectados": [round(lh, 2), round(la, 2)],
            "local": round(ph, 3), "empate": round(pd, 3), "visitante": round(pa, 3),
            "mas_2_5": round(po, 3), "pronostico": mejor}


def corners_proyectados(h, a):
    try:
        x = (h["corners_local"]["favor"] + a["corners_visitante"]["contra"]) / 2
        y = (a["corners_visitante"]["favor"] + h["corners_local"]["contra"]) / 2
        return [round(x, 1), round(y, 1), round(x + y, 1)]
    except TypeError:
        return None


def analizar(dia):
    partidos = api("/fixtures", ttl=1800, date=dia, timezone=ZONA)
    partidos = [p for p in partidos if p["league"]["id"] in LIGAS]
    print(f"{len(partidos)} partidos en las ligas elegidas para {dia}")
    salida = []
    for p in partidos:
        loc, vis = p["teams"]["home"], p["teams"]["away"]
        h, a = historial(loc["id"]), historial(vis["id"])
        h["top_tiros_arco"] = tiros_por_jugador(loc["id"])
        a["top_tiros_arco"] = tiros_por_jugador(vis["id"])
        item = {
            "liga": LIGAS[p["league"]["id"]],
            "hora": p["fixture"]["date"],
            "local": {"nombre": loc["name"], **h},
            "visitante": {"nombre": vis["name"], **a},
            "prediccion": pronostico(h, a),
            "corners_proyectados": corners_proyectados(h, a),
        }
        salida.append(item)
        pr = item["prediccion"]
        if pr:
            print(f"- {LIGAS[p['league']['id']]}: {loc['name']} vs {vis['name']} -> "
                  f"{pr['pronostico']} ({pr['local']:.0%} / {pr['empate']:.0%} / "
                  f"{pr['visitante']:.0%}), más de 2.5: {pr['mas_2_5']:.0%}")
    json.dump(salida, open("resultados.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("Guardado en resultados.json")


def buscar_ligas(pais):
    for x in api("/leagues", ttl=7 * 86400, country=pais, season=SEASON):
        print(x["league"]["id"], x["league"]["name"], f"({x['league']['type']})")


if __name__ == "__main__":
    if not KEY:
        sys.exit("Falta la clave: define la variable API_FOOTBALL_KEY")
    if len(sys.argv) > 2 and sys.argv[1] == "ligas":
        buscar_ligas(sys.argv[2])
    else:
        analizar(sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat())
    json.dump(cache, open(CACHE_FILE, "w"))