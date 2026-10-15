"""Temporada mínima por tienda, reposición por jerarquía de las liquidadoras (DH, SE, FB),
validación de las cargas de bloqueos y maestro de bloqueos."""

import io

import pandas as pd
import pytest

from forusight.data import bloqueos as B
from forusight.data import mantenedores as MT
from forusight.data import reporte as R
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params

TEMPS = [
    "INVIERNO 2025",
    "VERANO 2025",
    "ESCOLAR 2025",
    "INVIERNO 2026",
    "VERANO 2026",
    "ESCOLAR 2026",
    "INVIERNO 2027",
    "VERANO 2027",
    "FUTURA",
    None,
]


def _fuera(tienda: str, **kw) -> dict:
    n = len(TEMPS)
    _, fuera = B.fuera_de_surtido(
        pd.Series([tienda] * n),
        pd.Series(["M-C"] * n),
        pd.Series(TEMPS, dtype="string"),
        None,
        ["ESCOLAR 2026", "FUTURA"],
        "VERANO 2026",
        **kw,
    )
    return {str(t): not f for t, f in zip(TEMPS, fuera, strict=True)}  # temporada → se repone


def test_orden_comercial_no_es_el_del_anio_calendario():
    o = B.orden_temporada
    # INVIERNO (mar–sep) antes que VERANO (sep–mar) del mismo año; ESCOLAR va con su VERANO
    assert o("VERANO 2025") < o("INVIERNO 2026") < o("VERANO 2026") < o("INVIERNO 2027")
    assert o("ESCOLAR 2026") == o("VERANO 2026") and o("escolar  2026") == o("VERANO 2026")
    assert o("FUTURA") is None and o("NO COLECCION") is None and o("VERANO 23") is None
    assert B.es_temporada_minima("verano 2026") and not B.es_temporada_minima("ESCOLAR 2026")


def test_parametro_general_sin_cambios():
    """Tienda sin configuración: VERANO 2026 en adelante, ESCOLAR 2026 y FUTURA; sin temporada
    conocida se repone. Igual que antes del módulo."""
    r = _fuera("8")
    assert [t for t, ok in r.items() if ok] == [
        "VERANO 2026",
        "ESCOLAR 2026",
        "INVIERNO 2027",
        "VERANO 2027",
        "FUTURA",
        "None",
    ]


def test_liquidadoras_reponen_por_jerarquia_hasta_invierno_2026():
    """DH, SE y FB: todo lo anterior a VERANO 2026 y ESCOLAR 2026 (INVIERNO 2026 hacia atrás),
    aunque tengan una temporada mínima propia cargada por error."""
    kw = {"liquidadoras": {"111"}, "hasta_liquidadoras": "INVIERNO 2026"}
    r = _fuera("111", **kw, desde_por_tienda={"111": "VERANO 2027"})
    assert [t for t, ok in r.items() if ok] == [
        "INVIERNO 2025",
        "VERANO 2025",
        "ESCOLAR 2025",
        "INVIERNO 2026",
        "None",  # modelo sin temporada conocida: se repone
    ]
    # la regla de liquidadoras no toca a las demás tiendas
    assert _fuera("8", **kw) == _fuera("8")


def test_temporada_minima_por_tienda():
    r = _fuera("8", desde_por_tienda={"8": "INVIERNO 2026"})
    assert r["INVIERNO 2026"] and r["VERANO 2026"] and r["ESCOLAR 2026"] and r["FUTURA"]
    assert not r["VERANO 2025"] and not r["ESCOLAR 2025"]
    r = _fuera("8", desde_por_tienda={"8": "INVIERNO 2027"})
    # ESCOLAR 2026 de la lista general es anterior a la mínima: no se suma; FUTURA sí
    assert not r["VERANO 2026"] and not r["ESCOLAR 2026"]
    assert r["INVIERNO 2027"] and r["VERANO 2027"] and r["FUTURA"]
    assert _fuera("12", desde_por_tienda={"8": "INVIERNO 2027"}) == _fuera("12")


def test_motor_liquidadora_y_tienda_con_minima():
    e = Escenario()
    viejo = e.modelo("HP1-NEG", tallas=("38",))  # INVIERNO 2026
    actual = e.modelo("HP2-NEG", tallas=("38",))  # VERANO 2026
    escolar = e.modelo("HP3-NEG", tallas=("38",))  # ESCOLAR 2026
    for tid, nombre in (("111", "DH LURIN"), ("8", "HP JOCKEY"), ("12", "HP CHICLAYO")):
        e.tienda(tid, nombre=nombre)
        e.venta_constante(tid, viejo + actual + escolar, unidades=2)
        for s in viejo + actual + escolar:
            e.stock_tienda(tid, s, 0)
    for s in viejo + actual + escolar:
        e.stock_cd(s, 100)
    inp = e.inputs()
    tmp = {"HP1-NEG": "INVIERNO 2026", "HP2-NEG": "VERANO 2026", "HP3-NEG": "ESCOLAR 2026"}
    inp.dim_producto = inp.dim_producto.assign(
        temporada_comercial=inp.dim_producto["modelo_color_id"].map(tmp)
    )
    p = params(
        calendario={"aplicar": False},
        surtido={"temporada_desde_tienda": {"12": "INVIERNO 2026"}},
    )
    d = ejecutar(inp, p, CORTE).detalle.set_index(["tienda_id", "sku"])["cantidad"]
    # liquidadora: sólo INVIERNO 2026 hacia atrás
    assert d[("111", viejo[0])] > 0
    assert d[("111", actual[0])] == 0 and d[("111", escolar[0])] == 0
    # tienda con el parámetro general: VERANO 2026 y ESCOLAR 2026, no INVIERNO 2026
    assert d[("8", viejo[0])] == 0
    assert d[("8", actual[0])] > 0 and d[("8", escolar[0])] > 0
    # tienda con mínima INVIERNO 2026: las tres
    assert d[("12", viejo[0])] > 0 and d[("12", actual[0])] > 0 and d[("12", escolar[0])] > 0


def test_reporte_aplica_la_misma_regla():
    df = pd.DataFrame(
        {
            R.CENTRO: ["111", "111", "8"],
            "Código Modelo": ["HP1", "HP2", "HP1"],
            "Código Color": ["NEG", "NEG", "NEG"],
            "Temporada comercial": ["INVIERNO 2026", "VERANO 2026", "INVIERNO 2026"],
        }
    )
    regla = B.regla_temporadas(
        params().surtido, pd.DataFrame({"tienda_id": ["111", "8"], "cadena": ["DH", "HP"]}), ["DH"]
    )
    out, n = R.aplicar_surtido(df, None, ["ESCOLAR 2026"], "VERANO 2026", **regla)
    assert out["Reposición Bloqueada"].eq("SI").tolist() == [False, True, True] and n == 2


def test_tiendas_liquidadoras_del_catalogo_y_de_la_corrida():
    cat = B.tiendas_liquidadoras(None, ["DH", "SE", "FB"])
    assert {"20", "21", "88", "95", "96", "111", "159"} <= cat and "8" not in cat
    dim = pd.DataFrame(
        {"tienda_id": ["900", "111"], "nombre": ["DH NUEVA", "X"], "cadena": [None, "HP"]}
    )
    liq = B.tiendas_liquidadoras(dim, ["DH"])
    assert "900" in liq and "111" not in liq  # la corrida manda sobre el catálogo


# ------------------------------------------------------------------ mantenedor por tienda


def _excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def test_validar_temporadas_por_tienda():
    carga = MT.leer_carga_temporadas(
        _excel(
            pd.DataFrame(
                {
                    "Código de tienda": ["8", "008", "12", "111", "999", "", "16", "16", "7", "2"],
                    "Temporada mínima": [
                        "verano 2026",
                        "VERANO 2026",
                        "INVIERNO 2027",
                        "INVIERNO 2026",
                        "VERANO 2026",
                        "VERANO 2026",
                        "VERANO 2026",
                        "INVIERNO 2026",
                        "ESCOLAR 2026",
                        "VERANO 2026",
                    ],
                }
            )
        ),
        "t.xlsx",
    )
    tiendas = {"8", "12", "111", "16", "7", "2"}
    cambios, val = MT.validar_temporadas_tienda(
        carga, MT.ASIGNAR, {"2": "VERANO 2026"}, tiendas, {"111"}
    )
    r = dict(zip(val["fila"], val["resultado"], strict=True))
    assert r[2].startswith("OK") and r[3].startswith("Omitida: fila repetida")
    assert r[4].startswith("OK")
    assert "liquidadora" in r[5] and "no existe" in r[6] and "falta" in r[7]
    assert "distintas" in r[8] and "distintas" in r[9]
    assert "inválida" in r[10] and r[11].startswith("Omitida: la tienda ya tiene")
    assert cambios.to_dict("records") == [
        {"tienda_id": "8", "temporada_minima": "VERANO 2026"},
        {"tienda_id": "12", "temporada_minima": "INVIERNO 2027"},
    ]


def test_temporada_por_tienda_historial_y_quitar():
    h = pd.concat(
        [
            MT.nuevos_temporada_tienda(
                pd.DataFrame({"tienda_id": ["8", "12"], "temporada_minima": ["VERANO 2026"] * 2}),
                MT.ASIGNAR,
                "ana",
                "2026-10-14",
            ),
            MT.nuevos_temporada_tienda(
                pd.DataFrame({"tienda_id": ["12"], "temporada_minima": [""]}),
                MT.QUITAR,
                "ana",
                "2026-10-15",
            ),
        ],
        ignore_index=True,
    )
    h = MT.leer(MT.a_csv(h), MT.COLS_TEMPORADA_TIENDA)  # ida y vuelta por GitHub
    assert MT.temporada_tienda_vigente(h) == {"8": "VERANO 2026"}
    carga = pd.DataFrame({"fila": [2, 3], "tienda": ["8", "12"], "temporada": ["", ""]})
    cambios, val = MT.validar_temporadas_tienda(
        carga, MT.QUITAR, {"8": "VERANO 2026"}, {"8", "12"}, set()
    )
    assert cambios["tienda_id"].tolist() == ["8"]
    assert val["resultado"].iloc[1].startswith("Omitida: la tienda ya usa el parámetro general")
    # el Excel descargado (vacía = parámetro general) vuelve a leerse sin fijar nada
    plantilla = MT.plantilla_temporada_tienda(
        pd.DataFrame(
            {
                "tienda_id": ["8", "12"],
                "temporada_minima": ["VERANO 2026", ""],
                "nombre_tienda": ["A", "B"],
            }
        )
    )
    carga = MT.leer_carga_temporadas(plantilla, "x.xlsx")
    cambios, val = MT.validar_temporadas_tienda(
        carga, MT.ASIGNAR, {"8": "VERANO 2026"}, {"8", "12"}, set()
    )
    assert cambios.empty and val["resultado"].str.startswith("Omitida").all()


def test_leer_carga_temporadas_sin_columnas():
    with pytest.raises(ValueError, match="Cod Tienda"):
        MT.leer_carga_temporadas(_excel(pd.DataFrame({"x": [1]})), "x.xlsx")


# ------------------------------------------------------------------ bloqueos por Excel


EFECTIVOS = pd.DataFrame(
    {"tienda_id": ["8", "8", B.TODAS], "modelo_color_id": ["HP1-NEG", "HP2-NEG", "HP9-ROJ"]}
)


def _detalle(mc: list[str], tienda: list[str]) -> pd.DataFrame:
    return B.leer_carga_detalle(
        _excel(pd.DataFrame({"COD MOD COL": mc, "Código de tienda": tienda})), "c.xlsx"
    )


def test_carga_bloquear_valida_y_sin_duplicados():
    d = _detalle(
        ["hp3-neg", "HP3-NEG", "HP1-NEG", "HP9-ROJ", "", "HP 4 NEG X", "HP5", "HP6-NEG"],
        ["8", "008", "8", "16", "8", "8", "8", "999"],
    )
    v = B.validar_carga(d, B.BLOQUEAR, EFECTIVOS, {"8", "16"})
    assert v["resultado"].tolist() == [
        "OK: se bloquea",
        "Omitida: fila repetida en el archivo",  # 008 = 8
        "Omitida: ya está bloqueado",
        "Omitida: ya está bloqueado",  # bloqueado para todas las tiendas
        "Rechazada: falta COD MOD COL",
        "Rechazada: COD MOD COL inválido (formato MODELO-COLOR)",  # sin guion
        "Rechazada: COD MOD COL inválido (formato MODELO-COLOR)",
        "Rechazada: la tienda no existe en el maestro",
    ]
    assert B.validas(v).to_dict("records") == [{"tienda_id": "8", "modelo_color_id": "HP3-NEG"}]


def test_carga_desbloquear_solo_lo_bloqueado():
    d = _detalle(["HP1-NEG", "HP7-NEG", "HP9-ROJ", "HP9-ROJ", "HP2-NEG"], ["8", "8", "8", "", ""])
    v = B.validar_carga(d, B.DESBLOQUEAR, EFECTIVOS, {"8"})
    assert v["resultado"].tolist() == [
        "OK: se desbloquea",
        "Omitida: no está bloqueado",
        "Rechazada: está bloqueado para todas las tiendas; desbloquéalo con Cod Tienda vacío",
        "OK: se desbloquea",
        "OK: se desbloquea",  # vacío = en todas las tiendas donde esté
    ]
    ok = B.validas(v)
    manual = B.nuevos_desde_carga(ok, B.DESBLOQUEAR, "", "ana")
    ef = B.aplicar_manuales(EFECTIVOS, manual)
    assert ef.empty  # quedan desbloqueados exactamente los tres, nada más


def test_maestro_actualizado_al_14_10_2026():
    m = B.por_defecto()
    assert B.FECHA_POR_DEFECTO == "14/10/2026"
    assert len(m) == 224_832
    assert not m.duplicated(["tienda_id", "modelo_color_id"]).any()
    assert set(B.COLUMNAS_MAESTRO) <= set(m.columns)
    assert m["origen"].eq("reporte de bloqueos del 14/10/2026").all()
    assert m["modelo_color_id"].map(B._es_modelo_color).all() and m["tienda_id"].ne("").all()
    fila = m.loc[m["tienda_id"].eq("111") & m["modelo_color_id"].eq("39565-SL9")].iloc[0]
    assert (
        fila["motivo"] == "R10 ID465 Bloqueo Rosita 20260319"
        and fila["temporada"] == "INVIERNO 2020"
    )


def test_maestro_completo_descargable_y_se_vuelve_a_subir():
    base = pd.DataFrame(
        {
            "tienda_id": ["8", "111"],
            "modelo_color_id": ["HP1-NEG", "HP2-NEG"],
            "motivo": ["Concentración", "Coleccion No Activa en Tienda"],
            "temporada": ["VERANO 2026", ""],
            "origen": "reporte de bloqueos del 14/10/2026",
        }
    )
    manual = B.nuevos_desde_carga(
        pd.DataFrame({"tienda_id": [B.TODAS], "modelo_color_id": ["HP3-NEG"]}),
        B.BLOQUEAR,
        "x",
        "ana",
    )
    ef = B.aplicar_manuales(base, manual)
    cat = pd.DataFrame(
        {
            "codigo_tienda": ["8", "111"],
            "nombre_tienda": ["HP JOCKEY", "DH LURIN"],
            "cadena": ["HP", "DH"],
        }
    )
    m = B.maestro_completo(ef, manual, cat, pd.Series({"HP2-NEG": "INVIERNO 2025"}))
    assert len(m) == 3
    fila = m.set_index("COD MOD COL")
    assert (
        fila.loc["HP2-NEG", "Temporada comercial"] == "INVIERNO 2025"
    )  # del maestro de temporadas
    assert fila.loc["HP2-NEG", "Cadena"] == "DH"
    assert (
        fila.loc["HP3-NEG", "Tienda"] == "TODAS LAS TIENDAS"
        and fila.loc["HP3-NEG", "Usuario"] == "ana"
    )
    xlsx = B.maestro_excel(m, "15/10/2026")
    assert pd.read_excel(io.BytesIO(xlsx), sheet_name="Maestro bloqueos").shape == (3, 9)
    carga = B.leer_carga(xlsx, "maestro.xlsx")
    assert sorted(carga["modelo_color_id"]) == ["HP1-NEG", "HP2-NEG", "HP3-NEG"]
    assert carga.set_index("modelo_color_id").loc["HP3-NEG", "tienda_id"] == B.TODAS
