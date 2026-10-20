"""Tiendas, cadenas y marcas: el cruce que decide quién puede recibir qué.

Reglas (en este orden):
  1. **Tienda → nombre y cadena**: maestro `maestro_tiendas_table` de BigQuery; si una tienda
     no está ahí, el catálogo de tiendas de los reportes de distribución de Forus
     (`config/tiendas_forus.csv`). La cadena es la columna `cadena` del maestro o, si no
     viene, el prefijo del nombre (HP JOCKEY → HP), como en los reportes de distribución.
  2. **Sólo reciben tiendas identificadas** (con cadena conocida). Una bodega eComm o un
     código que no está en ningún maestro no recibe nada.
  3. **Qué se puede introducir en cada tienda**: el maestro `maestro_cadena_table`
     (código modelo → cadena) si existe; si no, la matriz marca × cadena
     (`config/cadenas.yaml`): un modelo sólo entra en tiendas cuya cadena vende su marca.
  4. La reposición de lo que la tienda ya tiene o vendió no se restringe.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"


@lru_cache(maxsize=1)
def catalogo_tiendas() -> pd.DataFrame:
    """Tiendas de los reportes de distribución: código, nombre, centro comercial, zona, cadena."""
    return pd.read_csv(CONFIG / "tiendas_forus.csv", dtype=str)


_ZONA_LIMA = {"LIMA", "CALLAO"}


def normalizar_maestro(m: pd.DataFrame) -> pd.DataFrame:
    """Maestro de tiendas de BigQuery → columnas del catálogo (codigo_tienda, nombre_tienda,
    centro_comercial, zona, cadena), sólo las tiendas de cadenas conocidas.

    - cadena numérica (stg_pe_perucentral_loca: 20 = AZALEIA) → prefijo del nombre;
    - zona que no es LIMA/PROVINCIA (ciudad: CUSCO, CALLAO…) → LIMA si es Lima o Callao;
    - bodegas, oficinas y cadenas sin marcas (BODEGA FORUS, PARFOIS…) quedan fuera."""
    from forusight.data.fuentes import codigo_tienda

    vacio = pd.Series(pd.NA, index=m.index, dtype="string")
    nombre = m["tienda_nombre"].astype("string").str.strip().str.upper()
    cadena = m.get("cadena", vacio).astype("string").str.strip().str.upper()
    cadena = cadena.where(~cadena.str.fullmatch(r"[0-9]+").fillna(True), prefijo(nombre))
    zona = m.get("zona", vacio).astype("string").str.strip().str.upper()
    zona = zona.where(
        zona.isin(["LIMA", "PROVINCIA"]).fillna(False),
        np.where(zona.isin(_ZONA_LIMA).fillna(False), "LIMA", "PROVINCIA"),
    ).where(zona.notna() & ~zona.str.fullmatch(r"[0-9]+").fillna(True))
    out = pd.DataFrame(
        {
            "codigo_tienda": m["tienda_cod"].map(codigo_tienda),
            "nombre_tienda": nombre,
            "centro_comercial": m.get("centro_comercial", vacio).astype("string").str.upper(),
            "zona": zona,
            "cadena": cadena,
        }
    )
    conocidas = set(marcas_por_cadena()) | set(catalogo_tiendas()["cadena"].astype(str))
    out = out.loc[out["cadena"].isin(conocidas).fillna(False) & out["codigo_tienda"].ne("")]
    return out.drop_duplicates("codigo_tienda").reset_index(drop=True)


def con_maestro(maestro: pd.DataFrame | None) -> pd.DataFrame:
    """Catálogo vigente: BigQuery manda (nombre, zona, cadena) y el archivo del repositorio
    completa lo que no trae (centro comercial) y las tiendas que no están en BigQuery."""
    cat = catalogo_tiendas()
    if maestro is None or not len(maestro):
        return cat
    m = maestro.set_index("codigo_tienda")
    c = cat.set_index("codigo_tienda")
    for col in ("centro_comercial", "zona", "cadena"):
        m[col] = m[col].fillna(m.index.to_series().map(c[col]))
    resto = c.loc[~c.index.isin(m.index)]
    return pd.concat([m, resto]).reset_index()[list(cat.columns)]


def tiendas_cerradas() -> set[str]:
    """Códigos de tiendas que ya cerraron (config/tiendas_cerradas.csv): la corrida nunca les
    envía aunque BigQuery todavía traiga su stock o su venta."""
    ruta = CONFIG / "tiendas_cerradas.csv"
    if not ruta.exists():
        return set()
    return set(pd.read_csv(ruta, dtype=str)["codigo_tienda"].str.strip().str.lstrip("0"))


@lru_cache(maxsize=1)
def _matriz_archivo() -> dict[str, list[str]]:
    raw = yaml.safe_load((CONFIG / "cadenas.yaml").read_text(encoding="utf-8")) or {}
    return raw.get("marcas_por_cadena", {}) or {}


def marcas_por_cadena(override: dict | None = None) -> dict[str, set[str]]:
    base = override if override else _matriz_archivo()
    return {str(c).strip().upper(): {str(m).strip().upper() for m in ms} for c, ms in base.items()}


def matriz_df(override: dict | None = None) -> pd.DataFrame:
    """Matriz larga (cadena, marca) para mostrar en pantalla."""
    filas = [(c, m) for c, ms in marcas_por_cadena(override).items() for m in sorted(ms)]
    return pd.DataFrame(filas, columns=["cadena", "marca"])


def prefijo(nombre: pd.Series) -> pd.Series:
    return nombre.astype("string").str.strip().str.split().str[0].str.upper()


def pares_permitidos(
    dim_tienda: pd.DataFrame,
    dim_producto: pd.DataFrame,
    cadena_modelo: pd.DataFrame | None,
    matriz: dict[str, set[str]],
) -> tuple[pd.DataFrame, str]:
    """(tienda_id, modelo_id) donde se puede introducir un modelo, y la regla usada."""
    t = dim_tienda.loc[dim_tienda["cadena"].notna(), ["tienda_id", "cadena"]]
    mm = pd.DataFrame([(c, m) for c, ms in matriz.items() for m in ms], columns=["cadena", "marca"])
    modelos = dim_producto[["modelo_id", "marca"]].dropna().drop_duplicates()
    modelos = modelos.assign(marca=modelos["marca"].astype("string").str.upper())
    por_matriz = t.merge(mm, on="cadena").merge(modelos, on="marca")[["tienda_id", "modelo_id"]]
    if cadena_modelo is not None and len(cadena_modelo):
        pares = t.merge(cadena_modelo[["modelo_id", "cadena"]], on="cadena")[
            ["tienda_id", "modelo_id"]
        ]
        # Marca que el maestro todavía no tiene (ningún modelo suyo, p. ej. AZALEIA nueva):
        # sus modelos usan la matriz marca × cadena. Las marcas del maestro no cambian.
        en_maestro = set(
            modelos.loc[modelos["modelo_id"].isin(set(cadena_modelo["modelo_id"])), "marca"]
        )
        nuevos = set(modelos.loc[~modelos["marca"].isin(en_maestro), "modelo_id"])
        sin_maestro = por_matriz.loc[por_matriz["modelo_id"].isin(nuevos)]
        pares = pd.concat([pares, sin_maestro], ignore_index=True)
        return pares.drop_duplicates(), "maestro modelo→cadena"
    return por_matriz.drop_duplicates(), "matriz marca × cadena"
