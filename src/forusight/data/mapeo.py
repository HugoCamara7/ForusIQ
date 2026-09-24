"""Mapeo de columnas de las tablas fuente de Forus a campos lógicos.

Los nombres de columna NO se asumen: se leen con INFORMATION_SCHEMA y se emparejan por
alias (algoritmo portado de Repo Control Center, validado contra las tablas de staging
de Forus). El mapeo resuelto se revisa en la página Conexión y se guarda.

Prioridad: ``[bigquery.mapeo.<fuente>]`` en los secrets > mapeo guardado > automático.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAPPING_PATH = Path(__file__).resolve().parents[3] / "data" / "bq_mapping.json"

#: Venta (tabla `ventas_table`). Alias tomados de Repo Control Center.
ALIAS_VENTAS: dict[str, list[str]] = {
    "fecha": [
        "fecmov_lv",  # silver.ft_pe_venta_retail
        "fecha",
        "fecha_venta",
        "fec_venta",
        "fecha_documento",
        "fecha_comprobante",
        "dia",
        "fecha_dia",
        "fec_doc",
        "fecha_transaccion",
        "fecha_emision",
        "fecha_corte",  # tablas *_bi (como stg_pe_central_stock_bi)
    ],
    "tienda_cod": [
        "nlocal_lv",  # silver.ft_pe_venta_retail (número de local)
        "cod_tienda",
        "codigo_tienda",
        "tienda",
        "cod_local",
        "local",
        "cod_sucursal",
        "sucursal_codigo",
        "id_tienda",
        "codigo_local",
        "id_local",
        "tienda_id",
    ],
    "unidades": [
        "unidades",
        "cantidad",
        "cant",
        "qty",
        "unidades_vendidas",
        "cantidad_vendida",
        "und",
        "cant_venta",
        "venta_unidades",
        "unidades_venta",
        "cantidad_venta",
        "venta_tiendas",
        "venta_und",
        "cantidad_unidades",
        "unidades_netas",
        "cantidad_neta",
    ],
    "id_producto": [
        "codpro_df",  # silver.ft_pe_venta_retail (código interno = CODINT_MA)
        "id_producto",
        "idproducto",
        "sku",
        "cod_producto",
        "codigo_producto",
        "codint_ma",
        "producto_id",
        "cod_sku",
        "codigo_sku",
        "sku_id",
        "codint",
    ],
    "cod_modelo": ["cod_modelo", "codigo_modelo", "modelo_cod", "cod_mod", "estilo_cod"],
    "cod_color": ["cod_color", "codigo_color", "color_cod"],
    "talla": ["talla", "talla_numero", "tallanumero", "size", "cod_talla", "talla_desc"],
    "marca": ["marca", "desc_marca", "nombre_marca"],
}

#: Maestro de productos ARTI (`product_master_table`), p. ej. stg_pe_central_arti.
ALIAS_ARTI: dict[str, list[str]] = {
    "id_producto": ["codint_ma", "codint", "id_producto", "idproducto", "sku"],
    "modcol": ["cod_mod_col", "codmod_codcol", "mod_col", "modelo_color", "codigo_modelo_color"],
    "cod_modelo": ["codmod_ma", "cod_modelo", "codigo_modelo", "modelo_cod", "cod_mod"],
    "cod_color": ["codcol_ma", "cod_color", "codigo_color", "color_cod"],
    "talla": ["talnum_ma", "talla_numero", "talla", "size", "cod_talla"],
    "marca": ["marca_ma", "marca", "desc_marca", "nombre_marca"],
    "genero": ["genero_ma", "genero", "desc_genero", "sexo"],
    "categoria": ["clase_ma", "clase", "desc_clase", "categoria", "tipo_ma", "tipo_prenda"],
    "descripcion": ["descripcion_ma", "descripcion", "desc_modelo", "nombre_modelo"],
    "color": ["color_ma", "desc_color", "nombre_color", "color"],
    "precio": ["precio_ma", "precio", "pvp", "precio_venta", "precio_lista"],
    "prenda": ["prenda_ma", "prenda", "desc_prenda", "subclase"],
    "temporada": ["temporada_comercial", "temporada_ma", "temporada", "temp_ma"],
}

#: Maestro tienda → nombre (`maestro_tiendas_table`).
ALIAS_TIENDAS: dict[str, list[str]] = {
    "tienda_cod": [
        "codigo_tienda",
        "cod_tienda",
        "codigo_centro",
        "cod_centro",
        "cod_local",
        "codigo_local",
        "id_tienda",
        "tienda",
    ],
    "tienda_nombre": [
        "nombre_tienda",
        "nombre_centro",
        "desc_tienda",
        "tienda_nombre",
        "nombre_local",
        "desc_local",
        "nombre",
    ],
    "centro_comercial": ["centro_comercial", "mall", "centro_comercial_nombre"],
    "zona": ["zona_cc", "zona", "region", "ubicacion"],
    "cadena": ["cadena", "cod_cadena", "tipo_cadena", "desc_cadena", "nombre_cadena"],
}

#: Maestro código/modelo → cadena (`maestro_cadena_table`).
ALIAS_CADENA: dict[str, list[str]] = {
    "cod_modelo": [
        "cod_modelo",
        "codigo_modelo",
        "codmod",
        "codmod_ma",
        "modelo_cod",
        "modelo",
        "codigo",
    ],
    "cadena": ["cadena", "cod_cadena", "tipo_cadena", "desc_cadena", "nombre_cadena"],
}

#: Stock por fecha de corte (`stock_table`), p. ej. stg_pe_central_stock_bi.
ALIAS_STOCK: dict[str, list[str]] = {
    "fecha": ["fecha_corte", "fec_corte", "fecha", "fecha_stock"],
    "tienda_cod": ["codigo_tienda", "cod_tienda", "cod_local", "tienda", "cod_bodega"],
    "tienda_nombre": ["concat_tienda", "nombre_tienda", "desc_tienda", "nombre_local"],
    "id_producto": ["id_producto", "codint", "codint_ma", "sku", "idproducto"],
    # En tienda cuenta sólo stock_tiendas; stock_bodega suma únicamente en el CD 320.
    "stock_tienda": ["stock_tiendas", "stock_tienda", "stock", "unidades_stock"],
    "stock_bodega": ["stock_bodega"],
    "transito": ["stock_transito", "transito", "en_transito", "cant_transito"],
    # CD 320: disponible (ya sin reservas) y cada reserva por separado.
    "disponible": ["disponible", "stock_disponible"],
    "reserva_pedidos": ["reserva_pedidos"],
    "reserva_retail": ["reserva_retail"],
    "reserva_wholesale": ["reserva_wholesale"],
    "reserva_multicanal": ["reserva_multicanal"],
    "reserva_ecommerce": ["reserva_ecommerce"],
}

#: Reservas del CD en stock_bi (se descuentan en la opción «tiendas+bodega-reservas»).
RESERVAS = (
    "reserva_pedidos",
    "reserva_retail",
    "reserva_wholesale",
    "reserva_multicanal",
    "reserva_ecommerce",
)

FUENTES = {
    "ventas": ALIAS_VENTAS,
    "arti": ALIAS_ARTI,
    "stock": ALIAS_STOCK,
    "tiendas": ALIAS_TIENDAS,
    "cadena": ALIAS_CADENA,
}


def faltantes(fuente: str, mapa: Mapping[str, str]) -> list[str]:
    """Campos imprescindibles que el mapeo no cubre."""
    if fuente == "ventas":
        f = [c for c in ("fecha", "unidades", "tienda_cod") if c not in mapa]
        if "id_producto" not in mapa and not all(
            c in mapa for c in ("cod_modelo", "cod_color", "talla")
        ):
            f.append("producto (id_producto, o cod_modelo + cod_color + talla)")
        return f
    if fuente == "arti":
        f = [c for c in ("id_producto", "talla") if c not in mapa]
        if "modcol" not in mapa and not all(c in mapa for c in ("cod_modelo", "cod_color")):
            f.append("modelo-color (modcol, o cod_modelo + cod_color)")
        return f
    if fuente == "stock":
        return [c for c in ("fecha", "tienda_cod", "id_producto", "stock_tienda") if c not in mapa]
    if fuente == "tiendas":
        return [c for c in ("tienda_cod", "tienda_nombre") if c not in mapa]
    if fuente == "cadena":
        return [c for c in ("cod_modelo", "cadena") if c not in mapa]
    raise KeyError(fuente)


# ------------------------------------------------------------------ emparejamiento


def _normaliza(texto: str) -> str:
    """`Nro. Pedido`, `NRO_PEDIDO` y `nroPedido` colapsan al mismo texto."""
    plano = unicodedata.normalize("NFKD", str(texto))
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return "".join(c for c in plano.lower() if c.isalnum())


def _sufijo_comun(columnas: list[str]) -> str:
    """Sufijo de staging que llevan TODAS las columnas (`_ma`, `_ph`), si lo hay."""
    candidatos = set()
    for c in columnas:
        if "_" in c:
            cola = c.rsplit("_", 1)[1]
            if 1 <= len(cola) <= 3 and cola.isalpha():
                candidatos.add(cola.lower())
    if len(candidatos) != 1:
        return ""
    sufijo = candidatos.pop()
    return sufijo if all(str(c).lower().endswith("_" + sufijo) for c in columnas) else ""


def _sin_sufijo(columna: str, sufijo: str) -> str:
    if sufijo and str(columna).lower().endswith("_" + sufijo):
        return str(columna)[: -(len(sufijo) + 1)]
    return str(columna)


def _puntaje(alias_norm: str, col_norm: str) -> int:
    if not alias_norm or not col_norm:
        return 0
    if alias_norm == col_norm:
        return 100
    if col_norm.startswith(alias_norm) or col_norm.endswith(alias_norm):
        return 85
    if alias_norm in col_norm:
        return 70
    if col_norm in alias_norm and len(col_norm) >= 4:
        return 60
    return 0


def mapear(columnas: list[str], alias: Mapping[str, list[str]]) -> dict[str, str]:
    """Campo lógico → columna real; las coincidencias más fuertes se asignan primero."""
    cols = [str(c) for c in columnas]
    sufijo = _sufijo_comun(cols)
    norm = {c: _normaliza(_sin_sufijo(c, sufijo)) for c in cols}
    norm_full = {c: _normaliza(c) for c in cols}
    candidatos = []
    for campo, nombres in alias.items():
        for i, n in enumerate(nombres):
            an = _normaliza(n)
            for c in cols:
                p = max(_puntaje(an, norm[c]), _puntaje(an, norm_full[c]))
                if p:
                    candidatos.append((p, -i, campo, c))
    candidatos.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
    salida: dict[str, str] = {}
    usadas: set[str] = set()
    for _p, _i, campo, col in candidatos:
        if campo in salida or col in usadas:
            continue
        salida[campo] = col
        usadas.add(col)
    return salida


# ------------------------------------------------------------------ persistencia


def cargar_guardado(path: Path = MAPPING_PATH) -> dict[str, dict[str, str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def guardar(fuente: str, tabla: str, mapa: Mapping[str, str], path: Path = MAPPING_PATH) -> None:
    """Guarda por ``fuente|tabla`` (en Streamlit Cloud el disco es efímero: usar secrets)."""
    actual = cargar_guardado(path)
    actual[f"{fuente}|{tabla}"] = dict(mapa)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(actual, ensure_ascii=False, indent=1), encoding="utf-8")


def resolver(
    fuente: str,
    tabla: str,
    columnas: list[str],
    secrets: Mapping[str, Any] | None = None,
    path: Path = MAPPING_PATH,
) -> tuple[dict[str, str], str]:
    """(mapeo, origen). Sólo se aceptan columnas que existen en la tabla."""
    existentes = set(columnas)
    try:
        desde_secrets = dict(((secrets or {}).get("bigquery", {}) or {}).get("mapeo", {}) or {})
        desde_secrets = dict(desde_secrets.get(fuente, {}) or {})
    except Exception:
        desde_secrets = {}
    auto = mapear(columnas, FUENTES[fuente])
    guardado = cargar_guardado(path).get(f"{fuente}|{tabla}", {})
    for origen, base in (("secrets", desde_secrets), ("guardado", guardado)):
        if base:
            mapa = {k: v for k, v in base.items() if v in existentes and k in FUENTES[fuente]}
            # completa con lo automático lo que el mapeo explícito no define
            for k, v in auto.items():
                if k not in base and v not in mapa.values():
                    mapa[k] = v
            return mapa, origen
    return auto, "automatico"


def a_toml(mapeos: Mapping[str, Mapping[str, str]]) -> str:
    """Bloques `[bigquery.mapeo.<fuente>]` listos para pegar en los secrets."""
    lineas = []
    for fuente, mapa in mapeos.items():
        lineas.append(f"[bigquery.mapeo.{fuente}]")
        lineas += [f'{campo} = "{col}"' for campo, col in mapa.items()]
        lineas.append("")
    return "\n".join(lineas)
