"""Mantenedor de rutas: ruta semanal por mall y excepciones por fecha (feriados, imprevistos)."""

import pandas as pd
import pytest

from forusight.config.settings import EngineParams
from forusight.data import calendario as CAL
from forusight.data import rutas as RU

BASE = RU.base_por_defecto()
JUE, MIE = "2026-10-08", "2026-10-07"  # jueves 08/10: feriado Combate de Angamos


def _exc(*filas):
    return RU.agregar(
        RU.excepciones_vacio(),
        pd.concat([RU.nuevas_excepciones([f], [m], a, "", "test") for f, m, a in filas]),
    )


def test_ruta_semanal_sin_excepciones():
    assert RU.dias_de("Plaza Norte", BASE) == "MA,JU"
    assert RU.despacha("Plaza Norte", JUE, BASE, None)
    assert not RU.despacha("Plaza Norte", MIE, BASE, None)


def test_feriado_nacional_y_excepcion_de_un_mall():
    exc = _exc((JUE, RU.TODOS, RU.SIN_DESPACHO), (JUE, "Larcomar", RU.DESPACHO_EXTRA))
    assert not RU.despacha("Plaza Norte", JUE, BASE, exc)  # TODOS: no sale
    assert RU.despacha("Larcomar", JUE, BASE, exc)  # la del mall manda sobre TODOS


def test_mover_un_despacho_por_feriado():
    mov = RU.mover_despacho(JUE, MIE, ["Plaza Norte", "Mega Plaza"], "Feriado", "ana")
    assert len(mov) == 4 and set(mov["accion"]) == {RU.SIN_DESPACHO, RU.DESPACHO_EXTRA}
    exc = RU.agregar(RU.excepciones_vacio(), mov)
    assert RU.despacha("Plaza Norte", MIE, BASE, exc) and not RU.despacha(
        "Plaza Norte", JUE, BASE, exc
    )
    assert RU.despacha("Plaza San Miguel", JUE, BASE, exc)  # los demás malls siguen igual
    with pytest.raises(ValueError):
        RU.mover_despacho(JUE, JUE, ["Plaza Norte"], "", "ana")


def test_la_ultima_excepcion_de_un_dia_reemplaza_a_la_anterior():
    exc = _exc((JUE, "Plaza Norte", RU.SIN_DESPACHO))
    exc = RU.agregar(exc, RU.nuevas_excepciones([JUE], ["Plaza Norte"], RU.DESPACHO_EXTRA, "", "x"))
    assert len(exc) == 1 and RU.despacha("Plaza Norte", JUE, BASE, exc)


def test_guardar_y_leer_es_ida_y_vuelta():
    exc = _exc((JUE, "Plaza Norte", RU.SIN_DESPACHO))
    leido = RU.leer_excepciones(RU.a_csv(exc))
    assert (
        leido["fecha"].iloc[0] == pd.Timestamp(JUE) and leido["accion"].iloc[0] == RU.SIN_DESPACHO
    )
    base = RU.leer_base(RU.a_csv(BASE))
    pd.testing.assert_frame_equal(base, RU.normalizar_base(BASE.copy()))
    assert RU.huella(BASE, exc) != RU.huella(BASE, RU.excepciones_vacio())


def test_ruta_semanal_se_valida():
    df = pd.DataFrame(
        {
            "mall": ["Lurín", "Nuevo", None],
            "patrones": ["lurin", "NUEVO", ""],
            "dias": ["mi, ma", "xx", ""],
        }
    )
    out = RU.normalizar_base(df)
    assert out["dias"].tolist() == ["MA,MI", ""] and out["patrones"].tolist() == ["LURIN", "NUEVO"]
    with pytest.raises(ValueError, match="repetido"):
        RU.normalizar_base(
            pd.DataFrame({"mall": ["A", "a"], "patrones": ["X", "Y"], "dias": ["LU", "MA"]})
        )
    with pytest.raises(ValueError, match="reconocer"):
        RU.normalizar_base(pd.DataFrame({"mall": ["A"], "patrones": [""], "dias": ["LU"]}))


def test_la_corrida_toma_el_feriado_y_el_despacho_movido():
    """HP PLAZA NORTE (44) repone MA y JU. Con el jueves 08/10 movido al miércoles 07/10, la
    corrida del jueves no la incluye y la del miércoles sí."""
    t = pd.DataFrame({"tienda_id": ["44", "8"], "nombre": ["HP PLAZA NORTE", "HP JOCKEY"]})
    p = EngineParams()
    hoy_jue = CAL.aplicar(t, JUE, p).set_index("tienda_id")["recibe_hoy"]
    assert hoy_jue["44"] and not hoy_jue["8"]
    exc = RU.agregar(RU.excepciones_vacio(), RU.mover_despacho(JUE, MIE, ["Plaza Norte"], "", "x"))
    jue = CAL.aplicar(t, JUE, p, None, BASE, exc).set_index("tienda_id")["recibe_hoy"]
    mie = CAL.aplicar(t, MIE, p, None, BASE, exc).set_index("tienda_id")["recibe_hoy"]
    assert not jue["44"] and mie["44"] and mie["8"]  # Jockey sigue con su miércoles


def test_ruta_semanal_editada_cambia_los_dias_de_la_tienda():
    t = pd.DataFrame({"tienda_id": ["111"], "nombre": ["DH LURIN"]})
    base = BASE.copy()
    base.loc[base["mall"].eq("Lurín"), "dias"] = "MA,MI"
    mar = CAL.aplicar(t, "2026-10-06", EngineParams(), None, base, RU.excepciones_vacio())
    assert mar["recibe_hoy"].iloc[0] and mar["dias_reposicion"].iloc[0] == "MA,MI"


def test_cada_tienda_del_catalogo_cae_en_un_mall():
    t = RU.tiendas_por_mall(BASE)
    assert set(t["mall"]) - {""} <= set(BASE["mall"])
    assert t.loc[t["codigo_tienda"].eq("20"), "mall"].iloc[0] == "Huallaga"


# ------------------------------------------------------------------ una semana en un cuadro


def test_cuadro_de_la_semana_marca_los_dias_de_la_ruta():
    tabla = RU.tabla_semana(BASE, RU.excepciones_vacio(), JUE)
    assert list(tabla.columns[1:]) == [
        "LU 05/10",
        "MA 06/10",
        "MI 07/10",
        "JU 08/10",
        "VI 09/10",
        "SA 10/10",
        "DO 11/10",
    ]
    fila = tabla.set_index("Mall").loc["Plaza Norte"]
    assert fila["MA 06/10"] and fila["JU 08/10"] and not fila["MI 07/10"]


def test_feriado_con_un_clic_y_se_quita_igual():
    fechas = RU.fechas_semana(JUE)
    exc = RU.marcar_feriados(RU.excepciones_vacio(), fechas, [pd.Timestamp(JUE)], "ana")
    assert RU.feriados(exc, fechas) == [pd.Timestamp(JUE)]
    assert not RU.despacha("Plaza Norte", JUE, BASE, exc)
    assert RU.marcar_feriados(exc, fechas, [], "ana").empty


def test_mover_un_despacho_desde_el_cuadro():
    """Feriado el jueves 08/10 y Plaza Norte sale el miércoles 07/10: se marca su casilla."""
    fechas = RU.fechas_semana(JUE)
    exc = RU.marcar_feriados(RU.excepciones_vacio(), fechas, [pd.Timestamp(JUE)], "ana")
    tabla = RU.tabla_semana(BASE, exc, JUE)
    tabla.loc[tabla["Mall"].eq("Plaza Norte"), "MI 07/10"] = True
    nuevas = RU.aplicar_semana(BASE, exc, JUE, tabla, "ana")
    assert RU.despacha("Plaza Norte", MIE, BASE, nuevas)
    assert not RU.despacha("Plaza Norte", JUE, BASE, nuevas)
    assert RU.feriados(nuevas, fechas) == [pd.Timestamp(JUE)]  # el feriado se conserva
    assert len(nuevas) == 2  # TODOS el jueves + Plaza Norte el miércoles
    cambios = RU.cambios_semana(BASE, nuevas, JUE)
    assert cambios[0].startswith("JU 08/10: feriado") and "Plaza Norte: sale MI 07/10" in cambios
    # volver a dejar la casilla como estaba borra la excepción
    tabla.loc[tabla["Mall"].eq("Plaza Norte"), "MI 07/10"] = False
    assert len(RU.aplicar_semana(BASE, nuevas, JUE, tabla, "ana")) == 1


def test_guardar_el_cuadro_sin_cambios_no_toca_otras_semanas():
    otra = _exc(("2026-10-15", "Larcomar", RU.SIN_DESPACHO))
    tabla = RU.tabla_semana(BASE, otra, JUE)
    assert RU.aplicar_semana(BASE, otra, JUE, tabla, "ana").equals(otra)
