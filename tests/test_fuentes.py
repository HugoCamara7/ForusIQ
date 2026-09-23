"""Conexión a tablas fuente (sin red): mapeo, SQL, transformación y repositorio."""

import io

import numpy as np
import pandas as pd
import pytest

from forusight.config.settings import AppSettings
from forusight.data import fuentes as F
from forusight.data import mapeo as M
from forusight.data.repository import FuentesRepository, params_usados
from forusight.engine.pipeline import ejecutar
from tests.builders import params

COLS_ARTI = [
    "CODINT_MA",
    "CODMOD_MA",
    "CODCOL_MA",
    "TALNUM_MA",
    "CODBAR_MA",
    "MARCA_MA",
    "GENERO_MA",
    "TIPO_MA",
    "DESCRIPCION_MA",
    "COLOR_MA",
]
COLS_STOCK = [
    "fecha_corte",
    "id_producto",
    "conca",
    "talla",
    "codigo_tienda",
    "CONCAT_TIENDA",
    "stock_tiendas",
    "stock_bodega",
]
COLS_VENTAS = ["fec_doc", "cod_local", "desc_local", "codint_ma", "cant_venta", "marca"]


def test_mapeo_columnas_reales_de_forus():
    a = M.mapear(COLS_ARTI, M.ALIAS_ARTI)
    assert a["id_producto"] == "CODINT_MA" and a["talla"] == "TALNUM_MA"
    assert a["cod_modelo"] == "CODMOD_MA" and a["cod_color"] == "CODCOL_MA"
    assert a["marca"] == "MARCA_MA" and a["genero"] == "GENERO_MA"
    s = M.mapear(COLS_STOCK, M.ALIAS_STOCK)
    assert s == {
        "fecha": "fecha_corte",
        "id_producto": "id_producto",
        "tienda_cod": "codigo_tienda",
        "tienda_nombre": "CONCAT_TIENDA",
        "stock_tienda": "stock_tiendas",
        "stock_bodega": "stock_bodega",
    }
    v = M.mapear(COLS_VENTAS, M.ALIAS_VENTAS)
    assert v["fecha"] == "fec_doc" and v["unidades"] == "cant_venta"
    assert v["tienda_cod"] == "cod_local" and v["id_producto"] == "codint_ma"
    for fuente, mapa in (("arti", a), ("stock", s), ("ventas", v)):
        assert M.faltantes(fuente, mapa) == []


def test_faltantes_detecta_lo_imprescindible():
    assert "unidades" in M.faltantes(
        "ventas", {"fecha": "f", "tienda_cod": "t", "id_producto": "p"}
    )
    assert any(
        "producto" in x
        for x in M.faltantes("ventas", {"fecha": "f", "unidades": "u", "tienda_cod": "t"})
    )
    assert any(
        "stock" in x
        for x in M.faltantes("stock", {"fecha": "f", "tienda_cod": "t", "id_producto": "p"})
    )


def test_prioridad_de_mapeo_secrets_guardado_automatico(tmp_path):
    ruta = tmp_path / "m.json"
    auto, origen = M.resolver("stock", "p.d.t", COLS_STOCK, {}, ruta)
    assert origen == "automatico" and auto["stock_bodega"] == "stock_bodega"
    M.guardar(
        "stock", "p.d.t", {**auto, "stock_tienda": "stock_bodega"} | {"stock_bodega": "x"}, ruta
    )
    guardado, origen = M.resolver("stock", "p.d.t", COLS_STOCK, {}, ruta)
    assert origen == "guardado" and "stock_bodega" not in guardado  # "x" no existe → se descarta
    sec = {"bigquery": {"mapeo": {"stock": {"stock_tienda": "stock_tiendas"}}}}
    desde_sec, origen = M.resolver("stock", "p.d.t", COLS_STOCK, sec, ruta)
    assert origen == "secrets" and desde_sec["stock_tienda"] == "stock_tiendas"
    assert desde_sec["fecha"] == "fecha_corte"  # se completa con lo automático


def test_sql_parametrizado_sin_select_estrella():
    a = M.mapear(COLS_ARTI, M.ALIAS_ARTI)
    s = M.mapear(COLS_STOCK, M.ALIAS_STOCK)
    v = M.mapear(COLS_VENTAS, M.ALIAS_VENTAS)
    sqls = [
        F.sql_arti("p.d.arti", a, True),
        F.sql_ventas("p.d.v", v, "p.d.arti", a, True),
        F.sql_cortes("p.d.s", s),
        F.sql_dias_con_stock("p.d.s", s, "p.d.arti", a, True),
        F.sql_stock_foto("p.d.s", s, "p.d.arti", a, True),
    ]
    for sql in sqls:
        assert "SELECT *" not in sql.upper()
    assert "@desde" in sqls[1] and "@hasta" in sqls[1] and "IN UNNEST(@marcas)" in sqls[1]
    assert "`MARCA_MA`" in sqls[0] and "GROUP BY 1" in sqls[0]
    assert "@fecha_foto" in sqls[4] and "`stock_tiendas`" in sqls[4] and "`stock_bodega`" in sqls[4]
    # sin marca en ventas: semijoin con ARTI filtrado por marca
    v2 = {k: x for k, x in v.items() if k != "marca"}
    assert "SELECT CAST(`CODINT_MA` AS STRING) FROM `p.d.arti`" in F.sql_ventas(
        "p.d.v", v2, "p.d.arti", a, True
    )


@pytest.mark.parametrize("malo", ["a b", "x`; DROP", "1col", ""])
def test_columna_invalida_no_se_interpola(malo):
    with pytest.raises(ValueError):
        F.sql_cortes("p.d.s", {"fecha": malo})


def test_tabla_placeholder_rechazada():
    with pytest.raises(ValueError):
        F.sql_cortes("PROYECTO.DATASET.TABLA", {"fecha": "f"})


def test_params_usados_no_confunde_prefijos():
    p = {"hasta": 1, "hasta_foto": 2, "desde": 0}
    assert params_usados("WHERE d < @hasta_foto", p) == {"hasta_foto": 2}
    assert params_usados("WHERE d < @hasta AND d >= @desde", p) == {"hasta": 1, "desde": 0}


def test_dias_con_stock_escala_por_frecuencia_de_fotos():
    lunes = pd.Timestamp("2026-09-07")
    diarias = pd.DataFrame({"fecha_corte": [lunes + pd.Timedelta(days=i) for i in range(7)]})
    semanal = pd.DataFrame({"fecha_corte": [lunes]})
    cs = pd.DataFrame(
        {
            "semana_inicio": [lunes],
            "tienda_cod": ["1"],
            "id_producto": ["9"],
            "cortes_con_stock": [5],
        }
    )
    assert F.dias_por_semana(cs, diarias)["dias_con_stock"].iat[0] == 5
    cs1 = cs.assign(cortes_con_stock=1)
    assert F.dias_por_semana(cs1, semanal)["dias_con_stock"].iat[0] == 7


def test_normalizaciones():
    assert [F.codigo_tienda(x) for x in ("018", "18.0", " 18 ", "bod")] == ["18", "18", "18", "BOD"]
    assert list(F.texto(pd.Series(["'00123", "5543976.0", " ", None])))[:2] == ["00123", "5543976"]
    o = F.talla_orden(pd.Series(["37", "37½", "37 1/2", "M"]))
    assert list(o[:3]) == [37.0, 37.5, 37.5] and o.iat[3] >= 1000


def test_archivo_stock_cd():
    df = pd.DataFrame(
        {
            "Cod. Modelo": ["A", "A"],
            "ID Producto": ["5543976", "5543977"],
            "Disponible": [10, 3],
            "Reserva eCommerce": [2, 0],
            "Res. Retail": [1, 0],
            "Res. Wholesale": [0, 0],
            "Res. Multicanal": [0, 1],
        }
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    cd = F.leer_stock_cd_archivo(buf.getvalue())
    fila = cd.set_index("sku").loc["5543976"]
    assert fila["fisico"] == 13 and fila["reservado"] == 3
    assert (cd["fisico"] - cd["reservado"] - cd["comprometido"]).tolist() == [10, 3]
    with pytest.raises(ValueError):
        F.leer_stock_cd_archivo(pd.DataFrame({"x": [1]}).to_csv(index=False).encode(), "x.csv")


# ------------------------------------------------------------------ repositorio de punta a punta

CORTE = pd.Timestamp("2026-09-21")
SECRETS = {
    "bigquery": {
        "project_id": "proj",
        "ventas_table": "proj.silver.ventas",
        "product_master_table": "proj.bronze.stg_pe_central_arti",
        "stock_table": "proj.bronze.stg_pe_central_stock_bi",
    }
}


def _datos_falsos():
    arti, rng = [], np.random.default_rng(0)
    for m in ("1001", "1002"):
        for t in ("36", "37", "38", "39"):
            arti.append(
                {
                    "id_producto": f"{m}{t}",
                    "cod_modelo": m,
                    "cod_color": "NEG",
                    "talla": t,
                    "marca": "AZALEIA",
                    "genero": "DAMA",
                    "categoria": "ZAPATO",
                    "descripcion": "X",
                    "color": "NEGRO",
                }
            )
    arti = pd.DataFrame(arti)
    semanas = [CORTE - pd.Timedelta(weeks=k) for k in range(1, 17)]
    ventas = pd.DataFrame(
        [
            {
                "semana_inicio": s.date(),
                "tienda_cod": tc,
                "id_producto": sku,
                "unidades": float(rng.poisson(2)),
            }
            for s in semanas
            for tc in ("018", "025")
            for sku in arti.id_producto
        ]
    )
    cortes = pd.DataFrame(
        {
            "fecha_corte": [(CORTE - pd.Timedelta(days=d)).date() for d in range(1, 113)],
            "filas": 100,
        }
    )
    dias = pd.DataFrame(
        [
            {"semana_inicio": s.date(), "tienda_cod": tc, "id_producto": sku, "cortes_con_stock": 7}
            for s in semanas
            for tc in ("018", "025")
            for sku in arti.id_producto
        ]
    )
    foto = pd.DataFrame(
        [
            {
                "tienda_cod": tc,
                "id_producto": sku,
                "tienda_nombre": f"T{tc}",
                "stock": 0.0 if tc == "025" else 3.0,
            }
            for tc in ("018", "025", "320", "0320")
            for sku in arti.id_producto
        ]
    )
    return {
        "arti": arti,
        "ventas": ventas,
        "cortes": cortes,
        "dias_con_stock": dias,
        "stock_foto": foto,
    }


class _FakeBQ:
    def __init__(self):
        self.settings = AppSettings(gcp_project="proj", marcas=["azaleia"])
        self.datos = _datos_falsos()
        self.consultas = []
        self.gb_leidos = 0.0

    def columnas(self, tabla):
        cols = {
            "proj.silver.ventas": COLS_VENTAS,
            "proj.bronze.stg_pe_central_arti": COLS_ARTI,
            "proj.bronze.stg_pe_central_stock_bi": COLS_STOCK,
        }[tabla]
        return pd.DataFrame({"column_name": cols})

    def query_df(self, sql, params=None, labels=None):
        self.consultas.append((labels["consulta"], sql, params))
        self.gb_leidos += 0.01
        return self.datos[labels["consulta"]].copy()


def test_repositorio_fuentes_produce_entradas_validas_y_el_motor_corre():
    fake = _FakeBQ()
    repo = FuentesRepository(client=fake, secrets=SECRETS)
    inp = repo.cargar_entradas(CORTE)
    nombres = [c[0] for c in fake.consultas]
    assert nombres == ["arti", "ventas", "cortes", "dias_con_stock", "stock_foto"]
    for _, sql, p in fake.consultas:
        assert "SELECT *" not in sql.upper()
        if "@marcas" in sql:
            assert p["marcas"] == ["AZALEIA"]
    assert fake.consultas[-1][2]["fecha_foto"].isoformat() == "2026-09-20"

    assert set(inp.dim_tienda["tienda_id"]) == {"18", "25"}  # el CD 320 no es tienda
    assert inp.stock_cd["fisico"].gt(0).all()  # 320 y 0320 se normalizan al mismo CD
    assert repo.ultimo_diagnostico.fecha_foto == "2026-09-20"
    res = ejecutar(inp, params(), CORTE, run_id="R")
    d = res.detalle
    assert (d.groupby("sku")["cantidad"].sum() <= 6).all()
    assert (d.query("tienda_id == '25'")["estado_mc"] == "QUIEBRE").all()
    assert d.query("tienda_id == '25'")["cantidad"].sum() > 0


def test_repositorio_fuentes_archivo_cd_reemplaza_foto_y_excluidas():
    fake = _FakeBQ()
    fake.settings = AppSettings(gcp_project="proj", tiendas_excluidas=["025"])
    repo = FuentesRepository(client=fake, secrets=SECRETS)
    archivo = pd.DataFrame(
        {"sku": ["100136"], "fisico": [5.0], "reservado": [2.0], "comprometido": [0.0]}
    )
    inp = repo.cargar_entradas(CORTE, stock_cd_archivo=archivo)
    assert inp.stock_cd["sku"].tolist() == ["100136"]
    assert set(inp.dim_tienda["tienda_id"]) == {"18"}


def test_repositorio_fuentes_errores_claros():
    with pytest.raises(ValueError, match="ventas_table"):
        FuentesRepository(client=_FakeBQ(), secrets={"bigquery": {}}).tablas()
    fake = _FakeBQ()
    fake.columnas = lambda t: pd.DataFrame({"column_name": ["solo_esto"]})
    with pytest.raises(ValueError, match="Mapeo incompleto"):
        FuentesRepository(client=fake, secrets=SECRETS).cargar_entradas(CORTE)
