"""Reporte de bloqueos y temporadas a reponer."""

import io

import pandas as pd
import xlsxwriter

from forusight.data import bloqueos as B
from forusight.data import reporte as R
from forusight.engine.pipeline import ejecutar
from tests.builders import CORTE, Escenario, params

FILA = [
    "HP1-NEG",
    "111",
    "8",
    "HP X",
    "HP JOCKEY",
    "",
    0,
    0,
    0,
    "CALZADO",
    "HUSH PUPPIES",
    "MUJER",
    "ZAPATOS",
    "VERANO 2021",
    "Coleccion No Activa en Tienda",
    "",
]


def _xlsx(filas, cabecera=True) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf)
    ws = wb.add_worksheet("Hoja1")
    r = 0
    if cabecera:
        ws.write_row(3, 0, ["Reporte:", "1003 - Modelos Bloqueados"])
        ws.write_row(
            6,
            0,
            [
                "Código modelo color (s)",
                "Código sku",
                "Código centro",
                "Descripción (s)",
                "Nombre centro (c)",
                "Pronóstico de demanda (pd)",
                "Stock físico equiv.",
                "Stock tránsito interno",
                "Stock físico CD",
                "Clase",
                "Marca",
                "Genero",
                "Prenda",
                "Temporada comercial",
                "Motivo bloqueo",
                "Modelo concentrado (id listado) (s)",
            ],
        )
        r = 7
    for i, f in enumerate(filas):
        ws.write_row(r + i, 0, f)
    wb.close()
    return buf.getvalue()


def test_lee_partes_con_y_sin_cabecera():
    a = B.leer(_xlsx([FILA, FILA[:1] + ["112"] + FILA[2:]]))
    b = B.leer(_xlsx([["HP2-ROJ", "222", "016"] + FILA[3:]], cabecera=False))
    u = B.unir([a, b])
    assert set(B.claves(u)) == {"8|HP1-NEG", "16|HP2-ROJ"}  # sin repetidos por talla


def test_bloqueo_manda_sobre_reponer_lo_vendido():
    e = Escenario()
    skus = e.modelo("HP1-NEG", tallas=("38",))
    e.tienda("8", nombre="HP JOCKEY")
    e.venta_constante("8", skus, unidades=2)
    e.stock_tienda("8", skus[0], 0)
    e.stock_cd(skus[0], 20)
    p = params(calendario={"aplicar": False})
    inp = e.inputs()
    assert ejecutar(inp, p, CORTE).detalle["cantidad"].sum() > 0
    inp.bloqueos = pd.DataFrame({"tienda_id": ["8"], "modelo_color_id": ["HP1-NEG"]})
    d = ejecutar(inp, p, CORTE).detalle
    assert d["cantidad"].sum() == 0 and set(d["motivo_codigo"]) == {"NO_BLOQUEO_TIENDA"}


def test_reporte_bloqueos_y_temporadas():
    df = pd.DataFrame(
        {
            R.CENTRO: ["8", "8", "12"],
            "Código Modelo": ["HP1", "HP2", "HP1"],
            "Código Color": ["NEG", "NEG", "NEG"],
            "Temporada comercial": ["VERANO 2026", "ESCOLAR 2025", "VERANO 2026"],
        }
    )
    bl = pd.DataFrame({"tienda_id": ["8"], "modelo_color_id": ["HP1-NEG"]})
    out, n = R.aplicar_surtido(df, bl, ["INVIERNO 2026", "VERANO 2026"])
    assert n == 2
    assert out["Reposición Bloqueada"].eq("SI").tolist() == [True, True, False]


def test_nivel_del_reporte_manda_si_esta():
    e = Escenario()
    skus = e.modelo("HP1-NEG", tallas=("38", "39"))
    e.tienda("8", nombre="HP JOCKEY")
    e.venta_constante("8", skus, unidades=1)
    for s in skus:
        e.stock_tienda("8", s, 1)
        e.stock_cd(s, 20)
    p = params(calendario={"aplicar": False})
    inp = e.inputs()
    inp.niveles_ref = pd.DataFrame(
        {"tienda_id": ["8", "8"], "sku": skus, "nivel_ref": [4.0, 1.0], "rop_ref": [3.0, 0.0]}
    )
    d = ejecutar(inp, p, CORTE).detalle.set_index("sku")
    assert d.loc[skus[0], "stock_objetivo"] == 4 and d.loc[skus[0], "cantidad"] == 3
    assert d.loc[skus[1], "cantidad"] == 0  # posición 1 > punto de reorden 0: no pide


def test_niveles_referencia_toma_el_ultimo_reporte_de_cada_tienda():
    def rep(tienda, nivel):
        return pd.DataFrame(
            {
                R.CENTRO: [tienda],
                R.SKU: ["111"],
                R.MAX: [nivel],
                R.ROP: [nivel - 1],
                "2026-09-14": [1],
            }
        )

    nv = R.niveles_referencia(
        [
            (rep("8", 2), pd.Timestamp("2026-09-23")),
            (rep("8", 3), pd.Timestamp("2026-09-25")),
            (rep("16", 5), pd.Timestamp("2026-09-24")),
        ]
    ).set_index("tienda_id")
    assert nv.loc["8", "nivel_ref"] == 3 and nv.loc["16", "nivel_ref"] == 5


def test_temporada_comercial_del_maestro_y_filtro():
    from forusight.data import temporadas as T
    from forusight.export.archivo import construir_tabla

    e = Escenario()
    viejo = e.modelo("HP1-NEG", tallas=("38",))
    nuevo = e.modelo("HP2-NEG", tallas=("38",))
    e.tienda("8", nombre="HP JOCKEY")
    e.venta_constante("8", viejo + nuevo, unidades=2)
    for s in viejo + nuevo:
        e.stock_tienda("8", s, 0)
        e.stock_cd(s, 20)
    p = params(calendario={"aplicar": False})
    inp = e.inputs()
    maestro = pd.Series({"HP1-NEG": "VERANO 2025", "HP2-NEG": "VERANO 2026"})
    inp.dim_producto = T.aplicar(inp.dim_producto, maestro)
    res = ejecutar(inp, p, CORTE)
    d = res.detalle.set_index("sku")
    assert d.loc[viejo[0], "cantidad"] == 0 and d.loc[viejo[0], "motivo_codigo"] == "NO_TEMPORADA"
    assert d.loc[nuevo[0], "cantidad"] > 0
    t = construir_tabla(res.detalle, inp.ventas, inp.dim_producto, res.tiendas, p, CORTE)
    assert set(t["Temporada comercial"]) == {"VERANO 2026"}  # lo que no se repone no se lista
    assert T.por_defecto().get("HP10201162490-N11") is not None  # maestro guardado


def test_mantenedor_bloquear_desbloquear_y_todas_las_tiendas():
    base = pd.DataFrame(
        {"tienda_id": ["8", "8"], "modelo_color_id": ["HP1-NEG", "HP2-NEG"], "motivo": "r"}
    )
    m = pd.concat(
        [
            B.nuevos_manuales(["16"], ["hp3-neg"], B.BLOQUEAR, "nuevo", "ana"),
            B.nuevos_manuales(["8"], ["HP1-NEG"], B.DESBLOQUEAR, "vuelve", "ana"),
            B.nuevos_manuales([B.TODAS], ["HP9-ROJ"], B.BLOQUEAR, "todas", "ana"),
        ],
        ignore_index=True,
    )
    ef = B.aplicar_manuales(base, m)
    assert set(B.claves(ef)) == {"8|HP2-NEG", "16|HP3-NEG", "*|HP9-ROJ"}
    t = pd.Series(["8", "44", "16"])
    mc = pd.Series(["HP1-NEG", "HP9-ROJ", "HP3-NEG"])
    bloq, _ = B.fuera_de_surtido(t, mc, None, ef, [])
    assert bloq.tolist() == [False, True, True]  # desbloqueado · todas las tiendas · manual
    # la última acción manda: se vuelve a bloquear HP1 en la tienda 8
    m2 = pd.concat([m, B.nuevos_manuales(["8"], ["HP1-NEG"], B.BLOQUEAR, "", "ana")])
    assert "8|HP1-NEG" in set(B.claves(B.aplicar_manuales(base, m2)))
    # el historial se guarda y se vuelve a leer igual
    csv = m2.to_csv(index=False).encode("utf-8-sig")
    assert B.leer_manuales(csv)["accion"].tolist() == m2["accion"].tolist()


def test_carga_excel_mod_col_y_tienda():
    import io

    import pandas as pd

    from forusight.data import bloqueos as B

    buf = io.BytesIO()
    pd.DataFrame(
        {"MOD-COL": ["hp1-n11", "HP2-AZU", "HP3-ROJ"], "Cod Tienda": ["008", None, "59"]}
    ).to_excel(buf, index=False)
    carga = B.leer_carga(buf.getvalue(), "carga.xlsx")
    assert carga.values.tolist() == [["8", "HP1-N11"], ["*", "HP2-AZU"], ["59", "HP3-ROJ"]]
    nuevos = B.nuevos_desde_carga(carga, B.BLOQUEAR, "colección", "ana")
    efectivos = B.aplicar_manuales(None, nuevos)
    tiendas = pd.Series(["8", "2", "2", "59"])
    mc = pd.Series(["HP1-N11", "HP1-N11", "HP2-AZU", "HP3-ROJ"])
    bloq, _ = B.fuera_de_surtido(tiendas, mc, None, efectivos, [])
    # HP1 sólo en la 8; HP2 sin tienda = todas; HP3 sólo en la 59
    assert bloq.tolist() == [True, False, True, True]
    # sin la columna Mod-Col el archivo se rechaza
    buf2 = io.BytesIO()
    pd.DataFrame({"x": [1]}).to_excel(buf2, index=False)
    import pytest

    with pytest.raises(ValueError):
        B.leer_carga(buf2.getvalue(), "malo.xlsx")
