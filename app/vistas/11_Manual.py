"""Manual de uso de Forusight y diccionario de columnas (docs/MANUAL.md)."""

from pathlib import Path

import streamlit as st
from app.components.ui import hero

from forusight.export.archivo import diccionario_excel

MANUAL = Path(__file__).resolve().parents[2] / "docs" / "MANUAL.md"
WORD = MANUAL.with_name("Manual_Forusight.docx")

hero(
    "Manual",
    "Cómo se usa Forusight, cómo calcula cada número y cómo compararlo con Neogística.",
    eyebrow="Ayuda",
)
texto = MANUAL.read_text(encoding="utf-8") if MANUAL.exists() else "Manual no encontrado."
c1, c2 = st.columns(2)
if WORD.exists():
    c1.download_button(
        "Descargar manual (Word)",
        WORD.read_bytes(),
        file_name="Manual_Forusight.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        icon=":material/description:",
        width="stretch",
        key="dl_manual",
    )
c2.download_button(
    "Descargar diccionario de columnas",
    data=diccionario_excel,
    file_name="Diccionario_Forusight.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    icon=":material/menu_book:",
    width="stretch",
    key="dl_diccionario_manual",
)
# Sin el título (ya está en la cabecera de la página).
st.markdown(texto.split("\n", 1)[1] if texto.startswith("# ") else texto)
