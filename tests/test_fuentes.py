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


def test_nota_de_credito_llega_negativa_al_motor():
    """La semana con nota de crédito (venta neta negativa) no se descarta ni se pone en 0."""
    lunes = list(pd.date_range("2026-06-29", periods=12, freq="7D"))
    ventas = pd.DataFrame(
        [
            {"semana_inicio": lunes[2], "tienda_id": "1", "sku": "A", "unidades": 3.0},
            {"semana_inicio": lunes[5], "tienda_id": "1", "sku": "A", "unidades": -1.0},
            # sólo nota de crédito y sin stock: igual debe llegar
            {"semana_inicio": lunes[7], "tienda_id": "1", "sku": "B", "unidades": -2.0},
        ]
    )
    stock = pd.DataFrame({"tienda_id": ["1"], "sku": ["A"], "stock_disponible": [1.0]})
    e = F.inferir_exposicion(ventas, stock, lunes)
    assert e.loc[e["sku"].eq("A"), "unidades"].sum() == 2.0  # 3 vendidos − 1 devuelto
    assert e.loc[e["sku"].eq("B"), "unidades"].sum() == -2.0


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
        "venta_diaria": pd.DataFrame(
            {
                "fecha": [(CORTE + pd.Timedelta(days=d)).date() for d in (1, 2)],
                "tienda_cod": ["008", "008"],
                "id_producto": [arti.id_producto.iloc[0]] * 2,
                "unidades": [10.0, 15.0],
            }
        ),
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
        "venta_diaria",
    ]
    assert inp.venta_diaria["unidades"].sum() == 25 and set(inp.venta_diaria["tienda_id"]) == {"8"}
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
    assert len(cat) == 62 and cat["codigo_tienda"].is_unique and set(cat["cadena"]) == set(m)
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


def test_mapeo_de_ft_pe_venta_retail():
    cols = F.COLUMNAS_CONOCIDAS[F.TABLA_VENTA_RETAIL]
    m = M.mapear(cols, M.ALIAS_VENTAS)
    assert m["fecha"] == "fecmov_lv" and m["tienda_cod"] == "nlocal_lv"
    assert m["id_producto"] == "codpro_df" and m["unidades"] == "unidades_venta"
    assert m["marca"] == "marca" and M.faltantes("ventas", m) == []
    sql = F.sql_ventas(F.TABLA_VENTA_RETAIL, m, "p.d.arti", M.mapear(COLS_ARTI, M.ALIAS_ARTI), True)
    assert "`fecmov_lv`" in sql and "`unidades_venta`" in sql and "`nlocal_lv`" in sql


def test_stock_bi_con_disponible_y_reservas():
    cols = [
        "id_producto",
        "conca",
        "codigo_tienda",
        "stock_tiendas",
        "stock_bodega",
        "reserva_pedidos",
        "reserva_retail",
        "reserva_wholesale",
        "reserva_multicanal",
        "disponible",
        "transito",  # stock_bi lo trae, pero el tránsito sale de los pedidos
        "talla",
        "fecha_corte",
        "valorizado",
        "reserva_ecommerce",
    ]
    s = M.mapear(cols, M.ALIAS_STOCK)
    assert s["disponible"] == "disponible" and "transito" not in s
    assert all(s[r] == r for r in M.RESERVAS)
    assert M.faltantes("stock", s) == []
    a = M.mapear(COLS_ARTI, M.ALIAS_ARTI)
    sql = F.sql_stock_foto("p.d.s", s, "p.d.arti", a, True)
    assert "AS disponible" in sql and "AS reserva_ecommerce" in sql


def test_nombre_del_modelo_desde_la_venta_si_arti_no_lo_trae():
    class Fake(_FakeBQ):
        def columnas(self, tabla):
            df = super().columnas(tabla)
            if tabla == VENTAS_T:
                df = pd.concat([df, pd.DataFrame({"column_name": ["modelo"]})], ignore_index=True)
            return df

    fake = Fake()
    fake.datos["arti"] = fake.datos["arti"].drop(columns=["descripcion"])
    sku = fake.datos["arti"]["id_producto"].iloc[0]
    fake.datos["nombres_modelo"] = pd.DataFrame({"id_producto": [sku], "nombre_modelo": ["space"]})
    inp = FuentesRepository(client=fake, secrets=SECRETS).cargar_entradas(CORTE)
    dp = inp.dim_producto.set_index("sku")
    mc = dp.loc[sku, "modelo_color_id"]
    assert set(dp.loc[dp["modelo_color_id"] == mc, "descripcion"]) == {"SPACE"}
    assert "nombres_modelo" in [c[0] for c in fake.consultas]


def test_mapeo_automatico_de_pedidos_peru_central():
    from forusight.data import mapeo

    h = mapeo.mapear(F.COLUMNAS_CONOCIDAS[F.TABLA_PEDIDOS_H], mapeo.ALIAS_PEDIDOS)
    d = mapeo.mapear(F.COLUMNAS_CONOCIDAS[F.TABLA_PEDIDOS_D], mapeo.ALIAS_PEDIDOS_DETALLE)
    assert h["nro_pedido"] == "nroped_ph" and h["tienda_destino"] == "codloc_ph"
    assert h["estado"] == "estado_ph" and h["clasificacion"] == "clasificacion_ph"
    assert h["fecha_recepcion"] == "fecrec_ph"
    assert d == {
        "nro_pedido": "nroped_pd",
        "id_linea": "iddeta_pd",
        "id_producto": "codint_pd",
        "estado_linea": "estado_pd",
        "cantidad_despachada": "candes_pd",
        "cantidad": "canped_pd",
    }
    assert not mapeo.faltantes("pedidos", h) and not mapeo.faltantes("pedidos_detalle", d)


def test_sql_pedidos_une_cabecera_y_detalle():
    m_h = {
        "nro_pedido": "nroped_ph",
        "tienda_destino": "codloc_ph",
        "estado": "estado_ph",
        "clasificacion": "clasificacion_ph",
        "fecha": "fecped_ph",
        "fecha_recepcion": "fecrec_ph",
    }
    m_d = {
        "nro_pedido": "nroped_pd",
        "id_producto": "codint_pd",
        "cantidad_despachada": "candes_pd",
        "cantidad": "canped_pd",
    }
    sql = F.sql_pedidos(F.TABLA_PEDIDOS_H, m_h, F.TABLA_PEDIDOS_D, m_d, F.TABLA_ARTI, {}, False)
    assert "JOIN" in sql and "h.`nroped_ph`" in sql and "d.`nroped_pd`" in sql
    assert "NULLIF(SAFE_CAST(d.`candes_pd` AS FLOAT64), 0)" in sql  # despachada o pedida
    assert "@desde_pedidos" not in sql  # todo el historial de pedidos
    assert "SAFE_CAST(h.`fecrec_ph` AS DATE) > @fecha_foto" in sql
    assert "GROUP BY 1, 2, 3, 4, 5, 6, 7, 8\n" in sql + "\n"
    assert "DATE_DIFF(@fecha_foto, SAFE_CAST(h.`fecped_ph` AS DATE), DAY)" in sql


M_H = {
    "nro_pedido": "nroped_ph",
    "tienda_destino": "codloc_ph",
    "estado": "estado_ph",
    "clasificacion": "clasificacion_ph",
    "fecha": "fecped_ph",
    "fecha_recepcion": "fecrec_ph",
}
M_D = {
    "nro_pedido": "nroped_pd",
    "id_linea": "iddeta_pd",
    "id_producto": "codint_pd",
    "cantidad_despachada": "candes_pd",
    "cantidad": "canped_pd",
}


def test_tablas_de_pedidos_sin_filas_repetidas():
    """Una línea copiada varias veces en staging cuenta una sola vez (SELECT DISTINCT)."""
    sql = F.sql_pedidos(F.TABLA_PEDIDOS_H, M_H, F.TABLA_PEDIDOS_D, M_D, F.TABLA_ARTI, {}, False)
    assert "(SELECT DISTINCT `nroped_pd`, `iddeta_pd`, `codint_pd`, `canped_pd`" in sql
    assert "(SELECT DISTINCT `nroped_ph`, `codloc_ph`, `estado_ph`" in sql


def test_revisar_transito_de_una_tienda():
    class Fake(_FakeBQ):
        def columnas(self, tabla):
            cols = {F.TABLA_PEDIDOS_H: F.COLUMNAS_CONOCIDAS[F.TABLA_PEDIDOS_H]}
            cols[F.TABLA_PEDIDOS_D] = F.COLUMNAS_CONOCIDAS[F.TABLA_PEDIDOS_D]
            return pd.DataFrame({"column_name": cols[tabla]})

    fake = Fake()
    fake.datos = {
        "revisar_transito": pd.DataFrame(
            {
                "nro_pedido": ["P1", "P2", "P3", "P4"],
                "tienda_cod": ["97"] * 4,
                "estado": ["1", "6. en Transporte", "4", "1"],
                "clasificacion": ["1", "1", "1", "4.-Devolucion CD"],
                "fecha_pedido": pd.to_datetime(["2026-10-01"] * 4),
                "fecha_recepcion": pd.to_datetime([None, None, "2026-09-20", None]),
                "id_linea": ["1", "1", "1", "1"],
                "id_producto": ["5"] * 4,
                "cantidad_pedida": [2.0, 3.0, 4.0, 5.0],
                "cantidad_despachada": [0.0, 3.0, 4.0, 0.0],
                "filas": [1, 2, 1, 1],
            }
        ),
        "locales_pedidos": pd.DataFrame(
            {
                "tienda_cod": ["097", "999"],
                "estado": ["1", "1"],
                "clasificacion": ["1", "1"],
                "pedidos": [2, 1],
                "unidades": [5.0, 1.0],
            }
        ),
    }
    sec = {
        "bigquery": {
            "project_id": "p",
            "pedidos_header_table": F.TABLA_PEDIDOS_H,
            "pedidos_detail_table": F.TABLA_PEDIDOS_D,
        }
    }
    repo = FuentesRepository(client=fake, secrets=sec)
    lineas, locales = repo.revisar_transito(
        "97", params().recepcion, pd.Timestamp("2026-10-02").date()
    )
    # en transporte (6) de hace 1 día cuenta (reciente); recepcionado y devolución no
    assert lineas["cuenta_transito"].tolist() == [True, True, False, False]
    assert lineas.loc[lineas["cuenta_transito"], "unidades"].sum() == 5  # 2 pedidas + 3 desp.
    assert lineas["repetida"].tolist() == [False, True, False, False]
    assert locales["en_catalogo"].tolist() == [True, False]  # 097 = tienda 97
    _, sql, p = fake.consultas[0]
    assert "IN UNNEST(@tiendas)" in sql and p["tiendas"] == ["97"]
    # comparación con el reporte: la 97 tiene tránsito 2 en el SKU 5 según Neogística
    rep = pd.DataFrame(
        {
            "Código SKU": ["5", "6"],
            "Código Centro": ["097", "97"],
            "Stock Trán. Int. [un]": [2, 1],
            "Stock Trán. Prov. [un]": [0, 0],
        }
    )
    res, tabla = F.comparar_transito(lineas, rep)
    assert res["transito_neogistica"] == 3 and res["transito_forusight"] == 5
    fila = tabla.set_index(["estado_cod", "clasif_cod"]).loc[(1, 1)]
    assert fila["unid_con_transito_neo"] == 2 and fila["estado"] == "Aprobado"


def test_transito_de_pedidos_por_estado_y_clasificacion():
    crudo = pd.DataFrame(
        {
            "tienda_cod": ["018", "18", "18", "18", "18", "18", "320"],
            "id_producto": ["0005", "5", "5", "5", "5", "5", "5"],
            "estado": ["1", "6. en Transporte", "4", "0", "Prerecepcionado", "2", "1"],
            "clasificacion": ["1", "3.-Traspaso tiendas", "1", "1", "2", "4.-Devolucion CD", "1"],
            "unidades": [2, 3, 5, 7, 1, 4, 9],
        }
    )
    p = F.a_pedidos(crudo, {"5"}, {"320"})
    assert set(p["estado"].dropna()) == {0, 1, 2, 4, 6, 7}
    tr = F.transito_de_pedidos(p, [1, 2, 3, 6, 7], [1, 2, 3])
    # aprobado 2 + en transporte 3 + prerecepcionado 1; fuera: recepcionado, creado,
    # devolución al CD y el propio CD
    assert tr.to_dict("records") == [{"tienda_id": "18", "sku": "5", "stock_transito": 6.0}]


def test_recepcionado_despues_del_corte_va_al_fisico():
    """Stock de fecha F = cierre de F; lo recepcionado después de F no está en ese stock: no es
    tránsito, se suma al stock físico."""
    crudo = pd.DataFrame(
        {
            "tienda_cod": ["18", "18", "18", "18"],
            "id_producto": ["5", "5", "5", "5"],
            "estado": ["4", "4", "6", "4"],
            "clasificacion": ["1", "1", "1", "4"],
            "recibido_post_corte": [True, False, False, True],
            "unidades": [2, 10, 3, 9],
        }
    )
    p = F.a_pedidos(crudo, {"5"}, set())
    tr = F.transito_de_pedidos(p, [1, 2, 3, 6, 7], [1, 2, 3])
    assert tr["stock_transito"].tolist() == [3.0]  # sólo el que va en transporte
    rec = F.recepcion_de_pedidos(p, [1, 2, 3])
    # 2 recibidos después del corte (la devolución al CD no cuenta)
    assert rec.to_dict("records") == [{"tienda_id": "18", "sku": "5", "recepcion_post_corte": 2.0}]


def test_pedido_abierto_antiguo_sigue_en_transito():
    """Todo el historial: un pedido que sigue aprobado, aunque sea antiguo, es mercadería
    reservada para la tienda; lo recepcionado ya está en el stock."""
    crudo = pd.DataFrame(
        {
            "tienda_cod": ["18", "18", "18"],
            "id_producto": ["5", "5", "5"],
            "estado": ["1", "6", "4"],
            "clasificacion": ["1", "1", "1"],
            "unidades": [2, 40, 7],
        }
    )
    p = F.a_pedidos(crudo, {"5"}, set())
    tr = F.transito_de_pedidos(p, [1, 2, 3, 6, 7], [1, 2, 3])
    assert tr["stock_transito"].tolist() == [42.0]


def test_en_transporte_solo_si_el_pedido_es_reciente():
    """6/7 antiguo = llegó y quedó sin cerrar; 6/7 reciente = en camino de verdad."""
    crudo = pd.DataFrame(
        {
            "tienda_cod": ["8", "8", "8"],
            "id_producto": ["5", "5", "5"],
            "estado": ["6", "7", "1"],
            "clasificacion": ["1", "1", "1"],
            "dias_pedido": [3, 40, 60],
            "unidades": [2, 5, 1],
        }
    )
    p = F.a_pedidos(crudo, {"5"}, set())
    tr = F.transito_de_pedidos(p, [1, 2, 3], [1, 2, 3], [6, 7], 15)
    assert tr["stock_transito"].tolist() == [3.0]  # 2 en transporte (3 días) + 1 aprobado


def test_codigo_pedido_por_nombre():
    s = pd.Series(["Prerecepcionado", "Recepcionado", "en Picking", "7", None])
    assert F.codigo_pedido(s, F._NOMBRES_ESTADO).tolist() == [7, 4, 2, 7, pd.NA]


def test_lo_recibido_no_es_transito_aunque_la_cabecera_siga_abierta():
    """Con fecha de recepción o con la línea en estado 4, la mercadería ya está en el stock
    de la tienda: no suma tránsito aunque la cabecera diga «en Transporte»."""
    crudo = pd.DataFrame(
        {
            "tienda_cod": ["8", "8", "8"],
            "id_producto": ["5", "5", "5"],
            "estado": ["6", "6", "6"],
            "clasificacion": ["1", "1", "1"],
            "con_recepcion": [False, True, False],
            "estado_linea": ["6", "6", "4"],
            "unidades": [2, 3, 4],
        }
    )
    p = F.a_pedidos(crudo, {"5"}, set())
    tr = F.transito_de_pedidos(p, [1, 2, 3, 6, 7], [1, 2, 3])
    assert tr["stock_transito"].tolist() == [2.0]
    m_d = M.mapear(F.COLUMNAS_CONOCIDAS[F.TABLA_PEDIDOS_D], M.ALIAS_PEDIDOS_DETALLE)
    assert m_d["estado_linea"] == "estado_pd"
    sql = F.sql_pedidos(F.TABLA_PEDIDOS_H, M_H, F.TABLA_PEDIDOS_D, m_d, F.TABLA_ARTI, {}, False)
    assert "h.`fecrec_ph` IS NOT NULL AS con_recepcion" in sql and "AS estado_linea" in sql
