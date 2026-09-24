"""Conexión a tablas de Forus (sin red): mapeo, SQL, exposición inferida, maestros."""

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
COLS_TIENDAS = ["COD_TIENDA", "NOMBRE_TIENDA", "CENTRO_COMERCIAL", "ZONA", "CADENA"]
COLS_CADENA = ["CODIGO_MODELO", "CADENA"]


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
    t = M.mapear(COLS_TIENDAS, M.ALIAS_TIENDAS)
    assert t["tienda_cod"] == "COD_TIENDA" and t["tienda_nombre"] == "NOMBRE_TIENDA"
    c = M.mapear(COLS_CADENA, M.ALIAS_CADENA)
    assert c == {"cod_modelo": "CODIGO_MODELO", "cadena": "CADENA"}
    for fuente, mapa in (("arti", a), ("stock", s), ("ventas", v), ("tiendas", t), ("cadena", c)):
        assert M.faltantes(fuente, mapa) == []


def test_prioridad_de_mapeo_secrets_guardado_automatico(tmp_path):
    ruta = tmp_path / "m.json"
    auto, origen = M.resolver("stock", "p.d.t", COLS_STOCK, {}, ruta)
    assert origen == "automatico"
    M.guardar("stock", "p.d.t", {**auto, "stock_bodega": "no_existe"}, ruta)
    guardado, origen = M.resolver("stock", "p.d.t", COLS_STOCK, {}, ruta)
    assert origen == "guardado" and "stock_bodega" not in guardado
    sec = {"bigquery": {"mapeo": {"stock": {"stock_tienda": "stock_tiendas"}}}}
    desde_sec, origen = M.resolver("stock", "p.d.t", COLS_STOCK, sec, ruta)
    assert origen == "secrets" and desde_sec["fecha"] == "fecha_corte"


def test_sql_parametrizado_sin_select_estrella():
    a = M.mapear(COLS_ARTI, M.ALIAS_ARTI)
    s = M.mapear(COLS_STOCK, M.ALIAS_STOCK)
    v = M.mapear(COLS_VENTAS, M.ALIAS_VENTAS)
    sqls = [
        F.sql_arti("p.d.arti", a, True),
        F.sql_ventas("p.d.v", v, "p.d.arti", a, True),
        F.sql_cortes("p.d.s", s),
        F.sql_stock_foto("p.d.s", s, "p.d.arti", a, True),
        F.sql_marcas("p.d.arti", a),
        F.sql_maestro("p.d.t", M.mapear(COLS_TIENDAS, M.ALIAS_TIENDAS)),
    ]
    for sql in sqls:
        assert "SELECT *" not in sql.upper()
    assert "@desde" in sqls[1] and "@hasta_foto" in sqls[1]  # incluye la semana en curso
    assert "IN UNNEST(@marcas)" in sqls[1] and "`MARCA_MA`" in sqls[0]
    assert "REGEXP_REPLACE(UPPER(TRIM(CAST(`CODINT_MA` AS STRING)))" in sqls[0]
    assert (
        "@fecha_foto" in sqls[3] and "AS stock_tienda" in sqls[3] and "AS stock_bodega" in sqls[3]
    )
    assert "@desde_foto" in sqls[2]
    assert (
        sqls[5].startswith("SELECT DISTINCT")
        and "CAST(`COD_TIENDA` AS STRING) AS tienda_cod" in sqls[5]
    )


@pytest.mark.parametrize("malo", ["a b", "x`; DROP", "1col", ""])
def test_columna_invalida_no_se_interpola(malo):
    with pytest.raises(ValueError):
        F.sql_cortes("p.d.s", {"fecha": malo})


def test_params_usados_no_confunde_prefijos():
    p = {"hasta": 1, "hasta_foto": 2, "desde": 0, "desde_foto": 3}
    assert params_usados("WHERE d < @hasta_foto", p) == {"hasta_foto": 2}
    assert params_usados("WHERE d >= @desde AND d < @hasta", p) == {"hasta": 1, "desde": 0}


def test_normalizaciones():
    assert [F.codigo_tienda(x) for x in ("018", "18.0", " 18 ", "bod")] == ["18", "18", "18", "BOD"]
    s = pd.Series(["0005438957", "5438957.0", " 5438957 ", "ab12", "'00123"])
    assert list(F.sku_canonico(s)) == ["5438957", "5438957", "5438957", "AB12", "123"]
    o = F.talla_orden(pd.Series(["37", "37½", "37 1/2", "M"]))
    assert list(o[:3]) == [37.0, 37.5, 37.5] and o.iat[3] >= 1000


def test_exposicion_inferida_sin_historial_de_stock():
    """Distingue falta de venta (stock sin venta) de falta de stock (vendió y hoy 0)."""
    lunes = list(pd.date_range("2026-06-29", periods=12, freq="7D"))
    ventas = pd.DataFrame(
        [
            # A: vendió semanas 3 y 6, hoy tiene stock → expuesto desde la semana 3
            {"semana_inicio": lunes[2], "tienda_id": "1", "sku": "A", "unidades": 2.0},
            {"semana_inicio": lunes[5], "tienda_id": "1", "sku": "A", "unidades": 1.0},
            # Q: vendió semanas 1..4 y hoy está en 0 → expuesto 1..4; después, quiebre
            *[
                {"semana_inicio": lunes[k], "tienda_id": "1", "sku": "Q", "unidades": 1.0}
                for k in range(4)
            ],
        ]
    )
    stock = pd.DataFrame(
        {"tienda_id": ["1", "1", "1"], "sku": ["A", "S", "Q"], "stock_disponible": [3.0, 5.0, 0.0]}
    )
    e = F.inferir_exposicion(ventas, stock, lunes).set_index(["sku", "semana_inicio"])
    dias = e["dias_con_stock"]
    assert dias.loc["A"].sum() == 7 * 10  # semanas 3..12
    assert dias.loc["Q"].sum() == 7 * 4  # sólo mientras vendía
    assert dias.loc["S"].sum() == 7 * 12  # stock sin venta: toda la ventana (falta de venta)
    assert "N" not in e.index.get_level_values(0)  # sin stock ni venta: nunca tuvo


def test_archivo_stock_cd():
    df = pd.DataFrame(
        {
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
    assert (cd["fisico"] - cd["reservado"] - cd["comprometido"]).tolist() == [10, 3]


# ------------------------------------------------------------------ repositorio de punta a punta

CORTE = pd.Timestamp("2026-09-21")
ARTI_T, STOCK_T = F.TABLA_ARTI, F.TABLA_STOCK
VENTAS_T = "forus-analitica-prod-datalake.silver.stg_pe_reporteria_ventas_tablon"
TIENDAS_T, CADENA_T = "p.maestros.tiendas", "p.maestros.modelo_cadena"
SECRETS = {
    "bigquery": {
        "enabled": True,
        "project_id": "forus-pe-shared-prod-ti",
        "table": ARTI_T,
        "ventas_table": VENTAS_T,
        "maestro_tiendas_table": TIENDAS_T,
        "maestro_cadena_table": CADENA_T,
    }
}


def _datos_falsos():
    arti = pd.DataFrame(
        [
            {
                "id_producto": f"{m}{t}",
                "cod_modelo": m,
                "cod_color": "111",
                "talla": t,
                "marca": "HUSH PUPPIES",
                "genero": "MUJER",
                "categoria": "CALZADO",
                "descripcion": f"MOD{m}",
                "color": "BLACK",
            }
            for m in ("HP1", "HP2")
            for t in ("360", "370", "380", "390")
        ]
    )
    rng = np.random.default_rng(0)
    semanas = [CORTE - pd.Timedelta(weeks=k) for k in range(0, 14)]  # incluye la semana en curso
    ventas = pd.DataFrame(
        [
            {
                "semana_inicio": s.date(),
                "tienda_cod": tc,
                "id_producto": sku,
                "unidades": float(rng.poisson(2)),
            }
            for s in semanas
            for tc in ("008", "012")
            for sku in arti.id_producto
            if sku.startswith("HP1")
        ]
    )
    cortes = pd.DataFrame({"fecha_corte": [(CORTE + pd.Timedelta(days=1)).date()], "filas": 1})
    foto = pd.DataFrame(
        [
            {
                "tienda_cod": tc,
                "id_producto": f"{sku}.0",
                "tienda_nombre": f"1-{tc}",
                "stock_tienda": 0.0 if tc == "012" else 3.0,
                "stock_bodega": 5.0,
            }
            for tc in ("008", "012", "320")
            for sku in arti.id_producto
        ]
    )
    tiendas = pd.DataFrame(
        {
            "tienda_cod": ["8", "12", "44"],
            "tienda_nombre": ["HP JOCKEY", "HP CHICLAYO", "HP PLAZA NORTE"],
            "centro_comercial": ["JOCKEY", "CHICLAYO", "PLAZA NORTE"],
            "zona": ["LIMA", "PROVINCIA", "LIMA"],
        }
    )
    cadena = pd.DataFrame({"cod_modelo": ["HP1", "HP2"], "cadena": ["HP", "HP"]})
    return {
        "arti": arti,
        "cortes": cortes,
        "stock_foto": foto,
        "ventas": ventas,
        "maestro_tiendas": tiendas,
        "maestro_cadena": cadena,
    }


class _FakeBQ:
    def __init__(self):
        self.settings = AppSettings(gcp_project="forus-pe-shared-prod-ti", marcas=["hush puppies"])
        self.datos = _datos_falsos()
        self.consultas = []
        self.gb_leidos = 0.0
        self.max_gb = 20

    def columnas(self, tabla):
        cols = {
            VENTAS_T: COLS_VENTAS,
            ARTI_T: COLS_ARTI,
            STOCK_T: COLS_STOCK,
            TIENDAS_T: COLS_TIENDAS,
            CADENA_T: COLS_CADENA,
        }[tabla]
        return pd.DataFrame({"column_name": cols})

    def query_df(self, sql, params=None, labels=None):
        self.consultas.append((labels["consulta"], sql, params))
        return self.datos[labels["consulta"]].copy()


def test_flujo_completo_bigquery_maestros_y_motor():
    fake = _FakeBQ()
    repo = FuentesRepository(client=fake, secrets=SECRETS)
    inp = repo.cargar_entradas(CORTE)
    assert [c[0] for c in fake.consultas] == [
        "arti",
        "cortes",
        "stock_foto",
        "ventas",
        "maestro_tiendas",
        "maestro_cadena",
    ]
    assert "historial" not in [c[0] for c in fake.consultas]  # el stock no tiene historial
    for _, sql, p in fake.consultas:
        assert "SELECT *" not in sql.upper()
        if "@marcas" in sql:
            assert p["marcas"] == ["HUSH PUPPIES"]
    dt = inp.dim_tienda.set_index("tienda_id")
    assert dt.loc["8", "nombre"] == "HP JOCKEY" and dt.loc["12", "zona"] == "PROVINCIA"
    assert dt.loc["8", "cadena"] == "HP"  # prefijo del nombre del maestro
    assert set(inp.permitidos["modelo_id"]) == {"HP1", "HP2"}
    assert inp.stock_cd["fisico"].eq(3 + 5).all()  # CD 320: sala + bodega
    res = ejecutar(inp, params(), CORTE, run_id="R")
    d = res.detalle
    assert (d.query("tienda_id == '12' and modelo_id == 'HP1'")["estado_mc"] == "QUIEBRE").all()
    assert d.groupby("sku")["cantidad"].sum().le(8).all()


def test_sin_tabla_de_venta_es_error_claro():
    sec = {"bigquery": {"table": ARTI_T}}
    with pytest.raises(ValueError, match="ventas_table"):
        FuentesRepository(client=_FakeBQ(), secrets=sec).cargar_entradas(CORTE)


def test_maestro_de_cadena_limita_introducciones():
    fake = _FakeBQ()
    fake.datos["maestro_cadena"] = pd.DataFrame({"cod_modelo": ["HP1"], "cadena": ["RKF"]})
    inp = FuentesRepository(client=fake, secrets=SECRETS).cargar_entradas(CORTE)
    res = ejecutar(inp, params(afinidad={"umbral_introduccion": 0.0}), CORTE, run_id="R")
    intro = res.detalle.loc[res.detalle["es_introduccion"]]
    assert intro.empty  # HP2 no tiene cadena y HP1 es de otra cadena: no se introduce


def test_sin_maestros_usa_catalogo_neogistica_y_matriz_marca_cadena():
    sec = {"bigquery": {k: v for k, v in SECRETS["bigquery"].items() if "maestro" not in k}}
    fake = _FakeBQ()
    # 999: código sin maestro ni catálogo (p. ej. bodega eComm); 2: RKF JOCKEY (no vende HP)
    extra = fake.datos["stock_foto"].query("tienda_cod == '008'").assign(tienda_cod="999")
    rkf = fake.datos["stock_foto"].query("tienda_cod == '008'").assign(tienda_cod="2")
    fake.datos["stock_foto"] = pd.concat([fake.datos["stock_foto"], extra, rkf])
    repo = FuentesRepository(client=fake, secrets=sec)
    inp = repo.cargar_entradas(CORTE)
    dt = inp.dim_tienda.set_index("tienda_id")
    assert dt.loc["8", "nombre"] == "HP JOCKEY" and dt.loc["8", "origen_tienda"] == "catálogo Forus"
    assert dt.loc["2", "cadena"] == "RKF" and dt.loc["12", "zona"] == "PROVINCIA"
    assert not dt.loc["999", "activa"] and pd.isna(dt.loc["999", "cadena"])  # no recibe
    tiendas_intro = set(inp.permitidos["tienda_id"])
    assert "2" not in tiendas_intro  # RKF no vende HUSH PUPPIES
    assert {"8", "12"} <= tiendas_intro
    notas = " ".join(repo.ultimo_diagnostico.notas)
    assert "matriz marca × cadena" in notas and "999" in notas
    res = ejecutar(inp, params(afinidad={"umbral_introduccion": 0.0}), CORTE, run_id="R")
    d = res.detalle
    assert d.query("tienda_id == '999'")["cantidad"].sum() == 0
    assert d.loc[d["es_introduccion"] & d["tienda_id"].eq("2"), "cantidad"].sum() == 0


def test_matriz_marca_cadena_de_neogistica():
    from forusight.data import cadenas as CAD

    m = CAD.marcas_por_cadena()
    assert "HUSH PUPPIES" in m["HP"] and "HUSH PUPPIES" in m["FB"]
    assert "HUSH PUPPIES" not in m["RKF"] and m["CLB"] == {"COLUMBIA"} and m["VANS"] == {"VANS"}
    cat = CAD.catalogo_tiendas()
    assert len(cat) == 55 and set(cat["cadena"]) == set(m)
    assert CAD.marcas_por_cadena({"AZ": ["azaleia"]}) == {"AZ": {"AZALEIA"}}


def test_arti_salta_la_tabla_de_ean_del_catalogo():
    cean = "forus-analitica-prod-datalake.bronze.stg_pe_central_cean"
    sec = {"bigquery": {"project_id": "p", "product_master_table": cean, "table": ARTI_T}}
    fake = _FakeBQ()
    base = fake.columnas
    fake.columnas = lambda t: (
        pd.DataFrame({"column_name": ["codint_ce", "codean_ce", "nomlar_ce", "id_producto"]})
        if t == cean
        else base(t)
    )
    assert FuentesRepository(client=fake, secrets=sec).tablas()["arti"] == ARTI_T


def test_tabla_de_venta_ilegible_da_error_con_sugerencias():
    fake = _FakeBQ()
    base = fake.columnas

    def columnas(tabla):
        if tabla == VENTAS_T:
            raise ValueError("sin columnas")
        return base(tabla)

    fake.columnas = columnas
    fake.tablas_del_dataset = lambda p, d, patron="": ["stg_pe_reporteria_ventas_tablon_v2"]
    with pytest.raises(ValueError, match="stg_pe_reporteria_ventas_tablon_v2"):
        FuentesRepository(client=fake, secrets=SECRETS).cargar_entradas(CORTE)


def test_stock_usa_el_ultimo_corte_y_descarta_el_del_anio_pasado():
    fake = _FakeBQ()
    hoy = (CORTE + pd.Timedelta(days=1)).date()
    fake.datos["cortes"] = pd.DataFrame(
        {"fecha_corte": [(CORTE - pd.Timedelta(days=364)).date(), hoy], "filas": [9, 9]}
    )
    FuentesRepository(client=fake, secrets=SECRETS).cargar_entradas(CORTE)
    foto = next(p for n, _, p in fake.consultas if n == "stock_foto")
    assert foto["fecha_foto"] == hoy


def test_sin_corte_de_este_anio_no_usa_el_del_anio_pasado():
    fake = _FakeBQ()
    fake.datos["cortes"] = pd.DataFrame(
        {"fecha_corte": [(CORTE - pd.Timedelta(days=364)).date()], "filas": [9]}
    )
    with pytest.raises(ValueError, match="corte de este año"):
        FuentesRepository(client=fake, secrets=SECRETS).cargar_entradas(CORTE)


def test_venta_atrasada_no_detiene_la_corrida_y_corre_la_ventana():
    fake = _FakeBQ()
    ultima = (CORTE - pd.Timedelta(days=40)).date()  # tabla de ventas atrasada ~6 semanas
    fake.datos["ventas"] = fake.datos["ventas"].assign(ultima_venta=ultima)
    repo = FuentesRepository(client=fake, secrets=SECRETS)
    repo.cargar_entradas(CORTE)
    consultas = [c for c in fake.consultas if c[0] == "ventas"]
    assert len(consultas) == 2  # se repite con la ventana que termina en la última venta
    lunes = ultima - pd.Timedelta(days=ultima.weekday())
    assert consultas[1][2]["hasta_foto"] == lunes
    assert repo.ultimo_diagnostico.corte_venta == lunes.isoformat()
    assert repo.ultimo_diagnostico.venta_hasta == ultima.isoformat()


def test_venta_filtra_marca_por_arti_y_trae_ultima_fecha():
    a = M.mapear(COLS_ARTI, M.ALIAS_ARTI)
    v = M.mapear(COLS_VENTAS, M.ALIAS_VENTAS)
    sql = F.sql_ventas("p.d.v", v, "p.d.arti", a, True)
    assert "AS ultima_venta" in sql and "`p.d.arti`" in sql


def test_sql_revisar_venta_compara_tabla_y_marca():
    a = M.mapear(COLS_ARTI, M.ALIAS_ARTI)
    v = M.mapear(COLS_VENTAS, M.ALIAS_VENTAS)
    resumen, muestra = F.sql_revisar_venta("p.d.v", v, "p.d.arti", a)
    assert "AS ultima_tabla" in resumen and "AS ultima_marca" in resumen
    assert "IN UNNEST(@marcas)" in resumen and "@ultima_marca" in muestra
    assert "SELECT *" not in (resumen + muestra).upper()


def test_mapeo_de_una_tabla_de_venta_bi():
    cols = ["fecha_corte", "id_producto", "conca", "talla", "codigo_tienda", "venta_tiendas"]
    m = M.mapear(cols, M.ALIAS_VENTAS)
    assert m["fecha"] == "fecha_corte" and m["unidades"] == "venta_tiendas"
    assert m["tienda_cod"] == "codigo_tienda" and M.faltantes("ventas", m) == []
    assert "unidades" not in M.mapear(["fecha", "venta_soles"], M.ALIAS_VENTAS)


def test_detecta_tabla_de_venta_fuera_de_bigquery():
    from forusight.data.bq_client import claves_fuera_de_bigquery

    sec = {
        "bigquery": {"ventas_table": "p.silver.tablon"},
        "forusight": {"marcas": ["HP"], "ventas_table": "p.bronze.stg_pe_central_ventas_bi"},
    }
    assert claves_fuera_de_bigquery(sec) == [
        ("forusight", "ventas_table", "p.bronze.stg_pe_central_ventas_bi")
    ]


def test_mapeo_de_una_tabla_de_hechos_de_venta():
    cols = ["fecha_transaccion", "codigo_local", "cod_sku", "cantidad_unidades", "importe_neto"]
    m = M.mapear(cols, M.ALIAS_VENTAS)
    assert m == {
        "fecha": "fecha_transaccion",
        "tienda_cod": "codigo_local",
        "id_producto": "cod_sku",
        "unidades": "cantidad_unidades",
    }
