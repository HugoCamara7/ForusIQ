"""Constructores de escenarios pequeños y legibles para los tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from forusight.config.settings import EngineParams
from forusight.engine.pipeline import EngineInputs

CORTE = pd.Timestamp("2026-09-21")
SEMANAS_12 = range(1, 13)


def semana(rel: int) -> pd.Timestamp:
    """Inicio de la semana ``rel`` (1 = última semana cerrada)."""
    return CORTE - pd.Timedelta(weeks=rel)


class Escenario:
    def __init__(self) -> None:
        self.prod: list[dict] = []
        self.tiendas: list[dict] = []
        self.ventas: dict[tuple, dict] = {}
        self.stock: dict[tuple, dict] = {}
        self.cd: dict[str, dict] = {}

    # -------------------------------------------------- dimensiones
    def modelo(
        self,
        mc: str,
        tallas=("36", "37", "38", "39", "40"),
        categoria="ZAPATO",
        genero="DAMA",
        rango="MEDIO",
        modelo_id: str | None = None,
    ) -> list[str]:
        mid = modelo_id or mc.split("-")[0]
        skus = []
        for t in tallas:
            sku = f"{mc}-{t}"
            self.prod.append(
                {
                    "sku": sku,
                    "modelo_id": mid,
                    "modelo_color_id": mc,
                    "color": "X",
                    "talla": str(t),
                    "talla_orden": float(t),
                    "categoria": categoria,
                    "genero": genero,
                    "rango_precio": rango,
                }
            )
            skus.append(sku)
        return skus

    def tienda(
        self,
        tid: str,
        cluster: str | None = "A",
        importancia: float = 0.5,
        activa: bool = True,
        max_unidades: float | None = None,
    ) -> None:
        self.tiendas.append(
            {
                "tienda_id": tid,
                "nombre": tid,
                "cluster": cluster,
                "formato": "MALL",
                "importancia_comercial": importancia,
                "activa": activa,
                "max_unidades_corrida": np.nan if max_unidades is None else max_unidades,
            }
        )

    # -------------------------------------------------- hechos
    def venta(self, tid: str, sku: str, rel: int, unidades: float, dias: int = 7) -> None:
        self.ventas[(rel, tid, sku)] = {
            "semana_inicio": semana(rel),
            "tienda_id": tid,
            "sku": sku,
            "unidades": float(unidades),
            "dias_con_stock": int(dias),
        }

    def venta_constante(self, tid: str, skus, semanas=SEMANAS_12, unidades=1.0, dias=7) -> None:
        for s in skus:
            for rel in semanas:
                self.venta(tid, s, rel, unidades, dias)

    def exposicion(self, tid: str, sku: str, semanas, dias: int = 7) -> None:
        for rel in semanas:
            self.venta(tid, sku, rel, 0.0, dias)

    def stock_tienda(self, tid: str, sku: str, stock: float, transito: float = 0.0) -> None:
        self.stock[(tid, sku)] = {
            "tienda_id": tid,
            "sku": sku,
            "stock_disponible": float(stock),
            "stock_transito": float(transito),
        }

    def stock_cd(
        self, sku: str, fisico: float, reservado: float = 0.0, comprometido: float = 0.0
    ) -> None:
        self.cd[sku] = {
            "sku": sku,
            "fisico": float(fisico),
            "reservado": float(reservado),
            "comprometido": float(comprometido),
        }

    def inputs(self) -> EngineInputs:
        for p in self.prod:
            self.cd.setdefault(
                p["sku"], {"sku": p["sku"], "fisico": 0.0, "reservado": 0.0, "comprometido": 0.0}
            )
        cols_v = ["semana_inicio", "tienda_id", "sku", "unidades", "dias_con_stock"]
        cols_s = ["tienda_id", "sku", "stock_disponible", "stock_transito"]
        return EngineInputs(
            ventas=pd.DataFrame(list(self.ventas.values()), columns=cols_v),
            stock_tienda=pd.DataFrame(list(self.stock.values()), columns=cols_s),
            stock_cd=pd.DataFrame(list(self.cd.values())),
            dim_producto=pd.DataFrame(self.prod),
            dim_tienda=pd.DataFrame(self.tiendas),
        )


def params(**overrides) -> EngineParams:
    """EngineParams por defecto (no depende de params.yaml) con overrides por sección."""
    base = EngineParams().model_dump()
    base["exhibicion"]["tallas_core_default"] = ["37", "38", "39"]
    base["exhibicion"]["tallas_core_por_genero"] = {"CABALLERO": ["40", "41", "42"]}
    for seccion, valores in overrides.items():
        base[seccion].update(valores)
    return EngineParams.model_validate(base)
