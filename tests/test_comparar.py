"""Comparación con el reporte de Neogística (sólo lectura)."""

import pandas as pd

from forusight.export import comparar as C


def _fila(sku, centro, q, tr=0, nivel=1, fis=0, **extra):
    return {
        "Código SKU": sku,
        "Código Centro": centro,
        "Nombre Centro": "HP JOCKEY",
        "Marca": "HUSH PUPPIES",
        "Cantidad Pedida Final [un]": q,
        "Stock Trán. Int. [un]": tr,
        "Nivel Máximo [un]": nivel,
        "Stock Físico [un]": fis,
        **extra,
    }


def test_compara_y_explica_las_diferencias():
    fs = pd.DataFrame(
        [
            _fila("1", "8", 1),  # igual
            _fila("2", "8", 1),  # Neogística tiene tránsito
            _fila("3", "8", 1),  # Neogística no tiene esa talla
        ]
    )
    neo = pd.DataFrame(
        [
            _fila("1", "008", 1),
            _fila("2", "008", 0, tr=1),
            _fila("9", "008", 2, **{"Marca": "COLUMBIA"}),  # otra marca: no se compara
        ]
    )
    c = C.comparar(fs, neo)
    assert c["resumen"]["unidades_forusight"] == 3 and c["resumen"]["unidades_neogistica"] == 1
    causa = c["detalle"].set_index("sku")["causa"].to_dict()
    assert causa == {"2": "Distinto tránsito", "3": "Neogística no tiene esa tienda × talla"}
    assert C.a_excel(c)[:2] == b"PK"
