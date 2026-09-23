"""Exportación de la distribución (formato de revisión). Formato WMS/ERP: PENDIENTE (k)."""

from __future__ import annotations

import io

import pandas as pd


def columnas_display(cd_id: str = "320") -> dict[str, str]:
    return {
        "tienda_id": "Tienda",
        "modelo_color_id": "Modelo",
        "sku": "SKU",
        "talla": "Talla",
        "stock_tienda": "Stock tienda",
        "venta_4s": "Venta 4S",
        "venta_12s": "Venta 12S",
        "demanda_semanal": "Demanda estimada (pares/sem)",
        "stock_objetivo": "Stock objetivo",
        "necesidad": "Necesidad",
        "stock_cd_disponible": f"Stock CD {cd_id}",
        "cantidad": "Cantidad a distribuir",
        "motivo_texto": "Motivo",
    }


def a_formato_display(
    detalle: pd.DataFrame, cd_id: str = "320", columna_cantidad: str = "cantidad"
) -> pd.DataFrame:
    cols = columnas_display(cd_id)
    df = detalle.copy()
    if columna_cantidad != "cantidad":
        df["cantidad"] = df[columna_cantidad]
    out = df[list(cols)].rename(columns=cols)
    out["Demanda estimada (pares/sem)"] = out["Demanda estimada (pares/sem)"].round(2)
    return out


def a_excel(
    detalle: pd.DataFrame,
    cd_id: str = "320",
    resumen: dict | None = None,
    columna_cantidad: str = "cantidad",
) -> bytes:
    """Excel con hoja de distribución (sólo filas con envío), hoja de no enviados y resumen."""
    disp = a_formato_display(detalle, cd_id, columna_cantidad)
    envio = disp["Cantidad a distribuir"] > 0
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        disp.loc[envio].to_excel(xw, sheet_name="Distribucion", index=False)
        disp.loc[~envio].to_excel(xw, sheet_name="No enviados", index=False)
        if resumen:
            pd.DataFrame(list(resumen.items()), columns=["Indicador", "Valor"]).to_excel(
                xw, sheet_name="Resumen", index=False
            )
    return buf.getvalue()


def a_csv(detalle: pd.DataFrame, cd_id: str = "320", columna_cantidad: str = "cantidad") -> bytes:
    disp = a_formato_display(detalle, cd_id, columna_cantidad)
    return disp.loc[disp["Cantidad a distribuir"] > 0].to_csv(index=False).encode("utf-8-sig")
