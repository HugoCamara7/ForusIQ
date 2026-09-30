import hashlib

import streamlit as st
import yaml
from app.components.estado import SETTINGS
from app.components.login import puede
from app.components.ui import hero
from pydantic import BaseModel, ValidationError

from forusight.config.settings import EngineParams, dump_params, save_params

hero(
    "Parámetros",
    "Reglas del motor: cobertura, curva de tallas, afinidad y topes. Todo sale de params.yaml.",
    eyebrow="Configuración",
)
ss = st.session_state


def widgets(modelo: BaseModel, prefijo: str) -> dict:
    """Widgets para campos escalares; listas y diccionarios se editan en YAML."""
    valores = {}
    for nombre, campo in type(modelo).model_fields.items():
        v = getattr(modelo, nombre)
        etiqueta = nombre.replace("_", " ").capitalize()
        k = f"{prefijo}.{nombre}"
        if isinstance(v, BaseModel):
            st.markdown(f"**{etiqueta}**")
            valores[nombre] = widgets(v, k)
        elif isinstance(v, bool):
            valores[nombre] = st.checkbox(etiqueta, value=v, key=k)
        elif isinstance(v, int):
            valores[nombre] = int(
                st.number_input(etiqueta, value=v, step=1, key=k, help=campo.description)
            )
        elif isinstance(v, float):
            valores[nombre] = float(
                st.number_input(
                    etiqueta, value=v, step=0.05, format="%.3f", key=k, help=campo.description
                )
            )
        elif isinstance(v, str) and nombre in OPCIONES:
            ops = OPCIONES[nombre]
            valores[nombre] = st.selectbox(
                etiqueta, ops, index=ops.index(v) if v in ops else 0, key=k
            )
        else:
            valores[nombre] = v
    return valores


#: Campos de texto con valores cerrados (se eligen en el formulario).
OPCIONES = {
    "componentes": [
        "disponible",
        "tiendas+bodega-reservas",
        "tiendas+bodega",
        "tiendas",
        "bodega",
    ],
    "redondeo_nivel": ["cercano", "arriba"],
}


def huella_params() -> str:
    return hashlib.sha1(ss.params.model_dump_json().encode()).hexdigest()[:8]


def _aplicar_yaml(clave: str) -> None:
    """Callback: corre antes del script, así el formulario ya se dibuja con los valores nuevos."""
    try:
        ss.params = EngineParams.model_validate(yaml.safe_load(ss[clave]) or {})
        ss.flash_params = ("ok", "YAML válido y aplicado a la sesión.")
    except (ValidationError, yaml.YAMLError) as exc:
        ss.flash_params = ("error", str(exc))


# Las claves llevan la huella de ss.params: si los parámetros cambian por otra vía (YAML,
# Conexión), el formulario se vuelve a crear con los valores vigentes en vez de conservar los
# viejos (que «Aplicar a la sesión» volvería a imponer).
VER = huella_params()
tab_form, tab_yaml = st.tabs(["Formulario", "YAML avanzado"])
with tab_form, st.form("form_params"):
    nuevos = {}
    for seccion in EngineParams.model_fields:
        with st.expander(seccion.replace("_", " ").capitalize()):
            nuevos[seccion] = widgets(getattr(ss.params, seccion), f"{VER}.{seccion}")
    if st.form_submit_button("Aplicar a la sesión", type="primary"):
        try:
            ss.params = EngineParams.model_validate(nuevos)
            st.success("Parámetros aplicados. Vuelve a ejecutar la corrida.")
        except ValidationError as exc:
            st.error(str(exc))

with tab_yaml:
    if flash := ss.pop("flash_params", None):
        (st.success if flash[0] == "ok" else st.error)(flash[1])
    clave_yaml = f"yaml_{huella_params()}"  # huella de AHORA (el formulario pudo cambiarla)
    st.text_area("params.yaml", value=dump_params(ss.params), height=480, key=clave_yaml)
    c1, c2 = st.columns(2)
    c1.button("Validar y aplicar YAML", on_click=_aplicar_yaml, args=(clave_yaml,))
    if c2.button(
        "Guardar en params.yaml", disabled=not puede("parametros"), help="Sólo administradores"
    ):
        ruta = save_params(ss.params, SETTINGS.params_path)
        st.success(f"Guardado en {ruta}. En despliegue se versionará en el dataset APP.")
    st.download_button("Descargar params.yaml", dump_params(ss.params), file_name="params.yaml")
