import pandas as pd
import streamlit as st
from app.components.estado import ajustes
from app.components.ui import chips, hero, html, kpi_row, section

from forusight.data import cadenas as CAD

ss = st.session_state
diag = ss.get("diagnostico") or {}
res = ss.get("resultado")

hero(
    "Tiendas y cadenas",
    "Quién puede recibir qué: cada tienda pertenece a una cadena y cada cadena vende ciertas "
    "marcas. Los modelos nuevos sólo entran donde la cadena vende la marca; lo que la tienda ya "
    "tiene o vendió se repone siempre.",
    eyebrow="Tiendas · cadenas · marcas",
)

matriz = CAD.marcas_por_cadena(ajustes().marcas_por_cadena)
tiendas = pd.DataFrame(diag.get("tiendas") or [])
if tiendas.empty:
    tiendas = (
        CAD.catalogo_tiendas()
        .rename(columns={"codigo_tienda": "tienda_id", "nombre_tienda": "nombre"})
        .assign(origen_tienda="catálogo Forus", activa=True)
    )
if res is not None and "tienda_id" in tiendas:
    env = res.detalle.groupby("tienda_id")["cantidad"].sum().rename("unidades")
    tiendas = tiendas.merge(env, on="tienda_id", how="left").fillna({"unidades": 0})
tiendas = tiendas[tiendas["origen_tienda"].notna()]  # sólo tiendas identificadas

todas_marcas = sorted({m for ms in matriz.values() for m in ms})
kpi_row(
    [
        ("Tiendas", f"{len(tiendas)}", "identificadas con cadena", "store"),
        ("Cadenas", f"{tiendas['cadena'].nunique()}", "HP, RKF, CLB, VANS…", "grid"),
        ("Marcas", f"{len(todas_marcas)}", "vendidas en alguna cadena", "layers"),
        (
            "Con envío",
            f"{int((tiendas.get('unidades', pd.Series(dtype=float)) > 0).sum())}",
            "tiendas que reciben en la corrida",
            "truck",
        ),
    ]
)

tab_m, tab_t = st.tabs(["Marcas por cadena", "Tiendas"])

with tab_m:
    section(
        "Qué marcas vende cada cadena",
        "Un modelo nuevo sólo se introduce en las tiendas de las cadenas marcadas.",
        "layers",
    )
    marcas_run = {m.upper() for m in (diag.get("marcas") or [])}
    if marcas_run:
        chips(
            [
                (
                    "ok" if any(m in ms for ms in matriz.values()) else "err",
                    f"{m} → " + (", ".join(c for c in sorted(matriz) if m in matriz[c]) or "—"),
                )
                for m in sorted(marcas_run)
            ]
        )
    cad = sorted(matriz)
    n_t = tiendas.groupby("cadena").size()
    cab = "".join(
        f"<th style='text-align:center'>{c}<br><small style='color:#93A3BC'>"
        f"{int(n_t.get(c, 0))} tdas</small></th>"
        for c in cad
    )
    filas = []
    for m in todas_marcas:
        celdas = "".join(
            "<td style='text-align:center;color:#16A34A;font-weight:900'>✓</td>"
            if m in matriz[c]
            else "<td style='text-align:center;color:#CBD5E1'>·</td>"
            for c in cad
        )
        resalta = " style='background:#F2F6FF'" if m in marcas_run else ""
        filas.append(f"<tr{resalta}><td><b>{m}</b></td>{celdas}</tr>")
    html(
        "<div class='card' style='overflow-x:auto'><table class='mini'><tr><th>Marca</th>"
        f"{cab}</tr>{''.join(filas)}</table></div>"
    )
    with st.expander("Cambiar la matriz"):
        st.caption("Pega esto en los secrets, bajo `[forusight]`, y edítalo.")
        st.code(
            "[forusight.marcas_por_cadena]\n"
            + "\n".join(
                f"{c} = [{', '.join(chr(34) + m + chr(34) for m in sorted(matriz[c]))}]"
                for c in cad
            ),
            language="toml",
        )

with tab_t:
    cadenas_ = sorted(tiendas["cadena"].dropna().unique())
    elegidas = st.pills("Cadena", cadenas_, selection_mode="multi", key="tc_cadenas")
    vista = tiendas if not elegidas else tiendas[tiendas["cadena"].isin(elegidas)]
    columnas = [
        c
        for c in ("tienda_id", "nombre", "cadena", "centro_comercial", "zona", "unidades")
        if c in vista.columns
    ]
    maximo = float(vista["unidades"].max()) if "unidades" in vista and len(vista) else 1.0
    st.dataframe(
        vista[columnas].sort_values(["cadena", "tienda_id"]),
        hide_index=True,
        width="stretch",
        height=520,
        column_config={
            "tienda_id": "Código",
            "nombre": "Tienda",
            "cadena": "Cadena",
            "centro_comercial": "Centro comercial",
            "zona": "Zona",
            "unidades": st.column_config.ProgressColumn(
                "Unidades sugeridas", format="%d", min_value=0, max_value=max(maximo, 1.0)
            ),
        },
    )
