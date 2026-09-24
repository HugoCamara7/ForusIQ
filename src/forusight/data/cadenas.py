"""Tiendas, cadenas y marcas: el cruce que decide quién puede recibir qué.

Reglas (en este orden):
  1. **Tienda → nombre y cadena**: maestro `maestro_tiendas_table` de BigQuery; si una tienda
     no está ahí, el catálogo de tiendas de los reportes de Neogística
     (`config/tiendas_neogistica.csv`). La cadena es la columna `cadena` del maestro o, si no
     viene, el prefijo del nombre (HP JOCKEY → HP), como los nombra Neogística.
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

import pandas as pd
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"


@lru_cache(maxsize=1)
def catalogo_tiendas() -> pd.DataFrame:
    """55 tiendas de los reportes de Neogística: código, nombre, centro comercial, zona, cadena."""
    return pd.read_csv(CONFIG / "tiendas_neogistica.csv", dtype=str)


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
    if cadena_modelo is not None and len(cadena_modelo):
        pares = t.merge(cadena_modelo[["modelo_id", "cadena"]], on="cadena")
        return pares[["tienda_id", "modelo_id"]].drop_duplicates(), "maestro modelo→cadena"
    mm = pd.DataFrame([(c, m) for c, ms in matriz.items() for m in ms], columns=["cadena", "marca"])
    modelos = dim_producto[["modelo_id", "marca"]].dropna().drop_duplicates()
    modelos = modelos.assign(marca=modelos["marca"].astype("string").str.upper())
    pares = t.merge(mm, on="cadena").merge(modelos, on="marca")
    return pares[["tienda_id", "modelo_id"]].drop_duplicates(), "matriz marca × cadena"
