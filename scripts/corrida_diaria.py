"""Corrida diaria programada: todos los días a las 6:30 de Lima, sin que nadie abra la app.

Lo lanza ``.github/workflows/corrida-diaria.yml``. Hace EXACTAMENTE lo que hace «Ejecutar
corrida» en la pantalla -- mismas funciones de ``app.components.estado``, mismo motor, mismo
Excel -- y deja el archivo con su fecha en el repositorio privado de datos
(``<prefix>/corridas/AAAA-MM/AAAA-MM-DD_Distribucion_Forusight.xlsx``), de donde lo lee el
panel «Corridas guardadas» de la app. No hay un segundo motor: si lo hubiera, el archivo de
las 6:30 y el de la pantalla podrían decir cosas distintas sin que nadie lo notara.

Los secrets llegan como un ``.streamlit/secrets.toml`` que el workflow escribe con el
secreto de Actions ``FORUSIGHT_SECRETS_TOML`` (el mismo contenido que los secrets de
Streamlit Cloud).

**El log de Actions es PÚBLICO** (el repositorio es público): aquí sólo se imprimen fecha,
estado y contadores. El detalle de un error va a un ``_ERROR.txt`` en el repositorio privado.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
for _ruta in (RAIZ, RAIZ / "src"):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

import pandas as pd  # noqa: E402

from forusight.data import corridas as C  # noqa: E402

#: Intentos (hora de Lima): 6:30, 7:00, 7:30 y 8:00. GitHub DESCARTA ejecuciones de un cron
#: (no las retrasa): medido en otro repositorio de Forus, entrega ~1 de cada 3. Con cuatro
#: intentos y la corrida idempotente, basta con que llegue uno.
MINUTO_ULTIMO_INTENTO = 8 * 60

GUARDAR, PRELIMINAR, ESPERAR = "guardar", "preliminar", "esperar"

#: Si la carga aún no trae el cierre de ayer, la MISMA ejecución espera y reintenta cada
#: ``MINUTOS_ENTRE_INTENTOS`` hasta el último intento (8:00). Así basta con que el cronjob
#: externo (cron-job.org → workflow_dispatch a las 6:30) llame una vez: el cron de GitHub
#: llega con horas de atraso (12/10: a las 15:22 de Lima en vez de las 6:30).
MINUTOS_ENTRE_INTENTOS = 10
PENDIENTE = "carga diaria pendiente: se reintenta"


def decidir(aviso_carga: str | None, ahora, forzar: bool = False) -> str:
    """Si la carga diaria de stock/venta todavía no trae el cierre de ayer, se espera al
    intento siguiente; en el último (o forzando) se guarda igual, marcado PRELIMINAR. Un
    archivo con el stock de anteayer no puede pasar por el bueno."""
    if not aviso_carga:
        return GUARDAR
    minuto = ahora.hour * 60 + ahora.minute
    return PRELIMINAR if forzar or minuto >= MINUTO_ULTIMO_INTENTO else ESPERAR


lunes_de = C.lunes_de


def limpiar(store, hoy) -> int:
    """Borra del repositorio de datos las corridas (y errores) de semanas anteriores: sólo
    se guarda la semana en curso. Devuelve cuántos archivos borró."""
    hoy = pd.Timestamp(hoy).normalize()
    archivos = []
    for i in range(C.MESES_A_LIMPIAR):
        archivos += store.listar(C.carpeta_del_mes(hoy - pd.DateOffset(months=i)))
    viejos = C.vencidas(archivos, hoy)
    for a in viejos:
        store.borrar(a["path"], a["sha"], f"forusight: borra {a['name']} (semana anterior)")
    return len(viejos)


def correr(ahora, store, fuente: str = "bigquery", forzar: bool = False) -> dict:
    """Una corrida. Devuelve {estado, ...} sin datos sensibles (va al log público)."""
    from app.components import estado as E
    from app.components.archivo import tabla_archivo

    from forusight.config.settings import load_params
    from forusight.export.archivo import a_excel_forusight

    hoy = pd.Timestamp(ahora.date())
    if C.ya_guardada(store.listar(C.carpeta_del_mes(hoy)), hoy):
        return {"estado": "ya estaba guardada", "fecha": f"{hoy:%Y-%m-%d}"}

    marcas = None  # como la pantalla: sólo con BigQuery se filtra por marca
    if fuente == "bigquery":
        opciones = [str(m) for m in E.marcas_arti(fuente)["marca"].dropna()]
        marcas = tuple(E.marcas_por_defecto(opciones, E.ajustes().marcas))
        if not marcas:
            raise RuntimeError("Ninguna marca de [forusight] marcas está en ARTI.")
    params = load_params(E.ajustes().params_path)
    corte = lunes_de(hoy).date().isoformat()
    res, diag = E.correr_motor(
        fuente,
        corte,
        params.model_dump_json(),
        None,
        "",
        marcas,
        hoy.date().isoformat(),
        E.clave_bloqueos(),
        "",
    )
    decision = decidir(diag.get("aviso_carga"), ahora, forzar)
    if decision == ESPERAR:
        return {"estado": PENDIENTE, "fecha": f"{hoy:%Y-%m-%d}"}
    entradas = E.cargar_entradas(fuente, corte, None, "", marcas)[0]
    tabla = tabla_archivo(res, entradas, params, E.ajustes().cd_id, None)
    if tabla.empty:  # fin de semana o feriado: ninguna tienda repone hoy; nada que guardar
        return {"estado": "hoy no sale el camión a ninguna tienda", "fecha": f"{hoy:%Y-%m-%d}"}
    contenido = a_excel_forusight(tabla, hoy, stock_al=res.resumen.get("stock_al"))
    ruta = store.guardar(
        C.ruta_diaria(hoy, preliminar=decision == PRELIMINAR),
        contenido,
        f"forusight: corrida diaria del {hoy:%d/%m/%Y}",
    )
    return {
        "estado": "guardada"
        + (" (PRELIMINAR: sin el cierre de ayer)" if decision == PRELIMINAR else ""),
        "fecha": f"{hoy:%Y-%m-%d}",
        "ruta": ruta,
        "unidades": int(res.resumen.get("unidades_a_distribuir", 0)),
        "tiendas": int(res.resumen.get("tiendas_con_envio", 0)),
    }


def con_reintentos(intento, reloj, dormir, al_reintentar=lambda: None) -> dict:
    """Corre ``intento(ahora)``; mientras la carga siga pendiente espera y vuelve a intentar,
    hasta el último intento (8:00 de Lima), donde ``decidir`` ya guarda PRELIMINAR."""
    while True:
        ahora = reloj()
        r = intento(ahora)
        if r.get("estado") != PENDIENTE:
            return r
        minuto = ahora.hour * 60 + ahora.minute
        espera = min(MINUTOS_ENTRE_INTENTOS, max(MINUTO_ULTIMO_INTENTO - minuto, 0))
        dormir(max(espera, 1) * 60)
        al_reintentar()


def codigo_http(exc: BaseException) -> str:
    """Sólo el código HTTP de un error de GitHub (401 token inválido, 403 sin permiso): el
    log es público, así que nunca el repositorio, la rama ni el mensaje."""
    codigo = getattr(exc.__cause__, "code", None)
    return f", GitHub respondió {codigo}" if isinstance(codigo, int) else ""


def main() -> int:
    from app.components import estado as E

    ahora = C.ahora_lima()
    forzar = os.environ.get("FORUSIGHT_FORZAR", "").strip().lower() in ("1", "true", "si", "sí")
    store = E._store_bloqueos()
    if store is None:
        print(
            "Falta el repositorio de datos ([forusight] github_repository / github_token o "
            "[ticketing]) en FORUSIGHT_SECRETS_TOML: no hay donde guardar el Excel."
        )
        return 1
    import time

    import streamlit as st

    def releer():  # el intento siguiente vuelve a leer BigQuery, no la caché
        st.cache_data.clear()
        st.cache_resource.clear()

    fuente = E.fuente_por_defecto()
    try:
        r = con_reintentos(
            lambda t: correr(t, store, fuente, forzar), C.ahora_lima, time.sleep, releer
        )
    except Exception as exc:  # el detalle va al repo privado; el log es público
        print(
            f"La corrida falló ({type(exc).__name__}{codigo_http(exc)}). "
            "Detalle en el repositorio de datos."
        )
        try:
            store.guardar(
                f"{C.carpeta_del_mes(ahora)}/{ahora:%Y-%m-%d_%H%M}_ERROR.txt",
                traceback.format_exc().encode("utf-8"),
                f"forusight: error en la corrida diaria del {ahora:%d/%m/%Y %H:%M}",
            )
        except Exception as exc2:
            print(f"Tampoco se pudo guardar el detalle del error{codigo_http(exc2)}.")
        return 1
    try:
        r["borradas de semanas anteriores"] = limpiar(store, ahora.date())
    except Exception as exc:  # no limpiar no invalida la corrida de hoy
        r["limpieza"] = f"falló ({type(exc).__name__})"
    print(" · ".join(f"{k}: {v}" for k, v in r.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
