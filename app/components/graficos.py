"""Gráficos con el estilo de Forusight: un solo tono de marca, una sola escala,
grilla recesiva y tooltip en cada marca (reglas de dataviz)."""

from __future__ import annotations

import altair as alt
import pandas as pd

from app.components.ui import BRAND_BLUE, BRAND_PRIMARY

TINTA = "#0B1B46"
TINTA_2 = "#64748B"
GRILLA = "#EEF2F8"


def _tema(chart: alt.Chart, alto: int) -> alt.Chart:
    return (
        chart.properties(height=alto, background="transparent")
        .configure_view(stroke=None)
        .configure_axis(
            labelColor=TINTA_2,
            titleColor=TINTA_2,
            gridColor=GRILLA,
            domainColor=GRILLA,
            tickColor=GRILLA,
            labelFontSize=11.5,
            titleFontSize=11.5,
            labelFontWeight=600,
        )
        .configure_legend(labelColor=TINTA, titleColor=TINTA_2, orient="top")
    )


def barras_ranking(
    df: pd.DataFrame,
    cat: str,
    val: str,
    titulo_val: str = "Unidades",
    top: int = 12,
    tooltip_extra: list[str] | None = None,
) -> alt.Chart:
    """Barras horizontales ordenadas, un solo color y etiqueta de valor al final."""
    d = df.nlargest(top, val)
    orden = d[cat].astype(str).tolist()
    alto = max(120, 28 * len(d))
    base = alt.Chart(d).encode(
        y=alt.Y(
            f"{cat}:N", sort=orden, title=None, axis=alt.Axis(labelLimit=190, labelOverlap=False)
        ),
        x=alt.X(f"{val}:Q", title=titulo_val, axis=alt.Axis(grid=True, tickCount=5)),
        tooltip=[
            alt.Tooltip(f"{cat}:N", title=cat.replace("_", " ").capitalize()),
            alt.Tooltip(f"{val}:Q", title=titulo_val, format=",.0f"),
        ]
        + [alt.Tooltip(c) for c in (tooltip_extra or [])],
    )
    barras = base.mark_bar(color=BRAND_BLUE, cornerRadiusEnd=4, height=16)
    textos = base.mark_text(align="left", dx=5, color=TINTA, fontWeight=800, fontSize=11.5).encode(
        text=alt.Text(f"{val}:Q", format=",.0f")
    )
    return _tema(barras + textos, alto)


def barras_apiladas_100(
    df: pd.DataFrame, cat: str, serie: str, val: str, colores: dict[str, str]
) -> alt.Chart:
    """Composición (partes de un todo) con orden y color fijos por serie."""
    orden = list(colores)
    alto = max(140, 30 * df[cat].nunique() + 40)
    ch = (
        alt.Chart(df)
        .mark_bar(height=18, stroke="#FFFFFF", strokeWidth=2)
        .encode(
            y=alt.Y(f"{cat}:N", title=None, sort="-x"),
            x=alt.X(
                f"sum({val}):Q",
                stack="normalize",
                title=None,
                axis=alt.Axis(format="%", tickCount=5),
            ),
            color=alt.Color(
                f"{serie}:N",
                title=None,
                sort=orden,
                scale=alt.Scale(domain=orden, range=[colores[k] for k in orden]),
            ),
            order=alt.Order("orden:Q"),
            tooltip=[
                alt.Tooltip(f"{cat}:N"),
                alt.Tooltip(f"{serie}:N"),
                alt.Tooltip(f"{val}:Q", format=",.0f"),
            ],
        )
        .transform_calculate(orden=f"indexof({orden!r}, datum.{serie})")
    )
    return _tema(ch, alto)


def gauge(valor: float, etiqueta: str = "") -> str:
    """Medio anillo SVG (0-1) para una sola cifra titular."""
    import math

    v = max(0.0, min(1.0, float(valor or 0)))
    r, cx, cy = 80, 100, 96
    ang = math.pi * (1 - v)
    x, y = cx + r * math.cos(ang), cy - r * math.sin(ang)
    arco = f"M {cx - r} {cy} A {r} {r} 0 0 1 {x:.2f} {y:.2f}" if v > 0 else ""
    return f"""<svg viewBox="0 0 200 118" width="100%" style="max-width:260px;display:block;margin:auto"
      role="img" aria-label="{etiqueta} {v:.0%}">
      <defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stop-color="{BRAND_PRIMARY}"/>
      <stop offset="1" stop-color="{BRAND_BLUE}"/></linearGradient></defs>
      <path d="M {cx - r} {cy} A {r} {r} 0 0 1 {cx + r} {cy}" fill="none" stroke="#E8EEFB"
        stroke-width="16" stroke-linecap="round"/>
      <path d="{arco}" fill="none" stroke="url(#gg)" stroke-width="16" stroke-linecap="round"/>
      <text x="{cx}" y="{cy - 12}" text-anchor="middle" font-size="34" font-weight="900"
        fill="{TINTA}">{v:.0%}</text>
      <text x="{cx}" y="{cy + 12}" text-anchor="middle" font-size="11" font-weight="700"
        fill="{TINTA_2}">{etiqueta}</text></svg>"""
