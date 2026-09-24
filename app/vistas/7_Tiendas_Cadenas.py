import pandas as pd
import streamlit as st
from app.components.estado import ajustes
from app.components.ui import chips, hero, html, issue_box, kpi_row, section

from forusight.data import cadenas as CAD

ss = st.session_state
diag = ss.get("diagnostico") or {}
res = ss.get("resultado")

hero(
    "Tiendas y cadenas",
    "El cruce que decide quién puede recibir qué: tienda → nombre y cadena, y cadena → marcas "
    "que vende. Sólo reciben tiendas identificadas; los modelos nuevos sólo entran donde la "
    "cadena vende la marca. La reposición de lo que la tienda ya tiene o vendió no se restringe.",
    eyebrow="Match de maestros",
)

matriz = CAD.marcas_por_cadena(ajustes().marcas_por_cadena)
tiendas = pd.DataFrame(diag.get("tiendas") or [])
en_corrida = len(tiendas) > 0
if not en_corrida:
    tiendas = (
        CAD.catalogo_tiendas()
        .rename(columns={"codigo_tienda": "tienda_id", "nombre_tienda": "nombre"})
        .assign(origen_tienda="catalogo Neogística", activa=True)
    )

if res is not None and en_corrida:
    env = res.detalle.groupby("tienda_id")["cantidad"].sum().rename("unidades")
    tiendas = tiendas.merge(env, on="tienda_id", how="left").fillna({"unidades": 0})

ident = tiendas["origen_tienda"].notna()
n_maestro = int(tiendas["origen_tienda"].eq("maestro").sum())
n_cat = int(tiendas["origen_tienda"].eq("catalogo Neogística").sum())
regla = diag.get("regla_introduccion") or "matriz marca × cadena"
kpi_row(
    [
        (
            "Tiendas identificadas",
            f"{int(ident.sum())}",
            "en la corrida" if en_corrida else "catálogo Neogística",
            "store",
        ),
        ("Desde maestro BigQuery", f"{n_maestro}", "maestro_tiendas_table", "table"),
        ("Desde catálogo Neogística", f"{n_cat}", "respaldo de los reportes", "file"),
        ("Sin identificar", f"{int((~ident).sum())}", "no reciben (bodegas, otros)", "alert"),
        (
            "Cadenas",
            f"{tiendas.loc[ident, 'cadena'].nunique()}",
            f"introducciones por {regla}",
            "grid",
        ),
    ]
)
if not en_corrida:
    issue_box(
        "info",
        "Vista previa con el catálogo de Neogística",
        "Ejecuta una corrida para ver el cruce real contra la venta y el stock de BigQuery.",
    )

tab_t, tab_m, tab_x = st.tabs(["Tiendas", "Marcas por cadena", "Cómo se hace el match"])

with tab_t:
    cadenas_ = sorted(tiendas.loc[ident, "cadena"].dropna().unique())
    elegidas = st.pills("Cadena", cadenas_, selection_mode="multi", key="tc_cadenas")
    vista = tiendas if not elegidas else tiendas[tiendas["cadena"].isin(elegidas)]
    vista = vista.assign(
        estado=vista["origen_tienda"]
        .map({"maestro": "Maestro BigQuery", "catalogo Neogística": "Catálogo Neogística"})
        .fillna("Sin identificar · no recibe")
    )
    columnas = [
        c
        for c in ("tienda_id", "nombre", "cadena", "centro_comercial", "zona", "estado", "unidades")
        if c in vista.columns
    ]
    st.dataframe(
        vista[columnas].sort_values(["cadena", "tienda_id"], na_position="last"),
        hide_index=True,
        width="stretch",
        column_config={
            "tienda_id": "Código",
            "nombre": "Tienda",
            "cadena": "Cadena",
            "centro_comercial": "Centro comercial",
            "zona": "Zona",
            "estado": "Origen",
            "unidades": st.column_config.ProgressColumn(
                "Unidades sugeridas",
                format="%d",
                min_value=0,
                max_value=float(max(vista.get("unidades", pd.Series([1])).max(), 1)),
            ),
        },
    )
    sin = tiendas.loc[~ident, "tienda_id"].tolist()
    if sin:
        issue_box(
            "warn",
            f"{len(sin)} códigos sin maestro ni catálogo",
            "No reciben envío. Si alguno es una tienda real, agrégalo al maestro de "
            "tiendas: " + ", ".join(map(str, sin[:30])),
        )

with tab_m:
    section(
        "Qué marcas vende cada cadena",
        "Tomado de los reportes de Neogística. Un modelo nuevo sólo se introduce en "
        "tiendas cuya cadena vende su marca.",
        "layers",
    )
    marcas_run = {m.upper() for m in (diag.get("marcas") or [])}
    todas = sorted({m for ms in matriz.values() for m in ms})
    cad = sorted(matriz)
    cab = "".join(f"<th style='text-align:center'>{c}</th>" for c in cad)
    filas = []
    for m in todas:
        celdas = "".join(
            "<td style='text-align:center;color:#16A34A;font-weight:900'>✓</td>"
            if m in matriz[c]
            else "<td style='text-align:center;color:#CBD5E1'>·</td>"
            for c in cad
        )
        resalta = " style='background:#F2F6FF'" if m in marcas_run else ""
        filas.append(f"<tr{resalta}><td><b>{m}</b></td>{celdas}</tr>")
    html(
        f"<div class='card' style='overflow-x:auto'><table class='mini'><tr><th>Marca</th>"
        f"{cab}</tr>{''.join(filas)}</table></div>"
    )
    if marcas_run:
        chips(
            [
                (
                    "ok" if any(m in ms for ms in matriz.values()) else "err",
                    f"{m}: " + (", ".join(c for c in cad if m in matriz[c]) or "ninguna cadena"),
                )
                for m in sorted(marcas_run)
            ]
        )
    sin_matriz = sorted(set(tiendas.loc[ident, "cadena"].dropna()) - set(matriz))
    if sin_matriz:
        issue_box(
            "warn",
            "Cadenas sin marcas definidas",
            f"{', '.join(sin_matriz)}: no reciben introducciones hasta definirlas.",
        )
    with st.expander("Cambiar la matriz (pegar en secrets)"):
        st.caption(
            "Para ajustar la matriz sin tocar el código, pega esto en los secrets bajo "
            "`[forusight]` y edítalo."
        )
        st.code(
            "[forusight.marcas_por_cadena]\n"
            + "\n".join(
                f"{c} = [{', '.join(repr(m).replace(chr(39), chr(34)) for m in sorted(matriz[c]))}]"
                for c in cad
            ),
            language="toml",
        )

with tab_x:
    section("Reglas del cruce", "En este orden", "shield")
    issue_box(
        "info",
        "1 · Tienda → nombre y cadena",
        "Maestro de tiendas de BigQuery (`maestro_tiendas_table`: código tienda, nombre, "
        "y si existen centro comercial, zona y cadena). Si una tienda no está, se usa el "
        "catálogo de los reportes de Neogística. Sin columna cadena, la cadena es el "
        "prefijo del nombre (HP JOCKEY → HP).",
    )
    issue_box(
        "info",
        "2 · Sólo reciben tiendas identificadas",
        "Códigos que no están en ningún maestro (bodegas eComm, outlets) no reciben.",
    )
    issue_box(
        "info",
        "3 · Qué se puede introducir",
        "Maestro código modelo → cadena (`maestro_cadena_table`) si existe; si no, la "
        "matriz marca × cadena de la pestaña anterior.",
    )
    issue_box(
        "ok",
        "4 · La reposición no se restringe",
        "Lo que la tienda ya tiene o vendió se repone aunque la marca no esté en la matriz.",
    )
