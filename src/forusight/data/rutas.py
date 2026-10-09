"""Mantenedor de rutas: qué días despacha el CD a cada centro comercial (mall) y excepciones por
fecha (feriados, imprevistos).

* **Ruta semanal** (``rutas/rutas_mall.csv`` en el repositorio de datos; si no hay, la de
  ``config/rutas_mall.csv``): por mall, los días de la semana (``LU,MI,VI``) y los textos que lo
  reconocen en el centro comercial, la zona o el nombre de la tienda (``JOCKEY``). Todas las
  tiendas de un mall reciben los mismos días: el camión sale por mall.
* **Excepciones** (``rutas/excepciones.csv``): por fecha y mall (o ``TODOS``), ``sin_despacho``
  (feriado: ese día no sale el camión aunque toque) o ``despacho_extra`` (ese día sí sale aunque
  no toque). Mover un despacho por feriado = un ``sin_despacho`` en la fecha original + un
  ``despacho_extra`` en la nueva. La excepción de un mall manda sobre la de ``TODOS``.

Las dos tablas se guardan en GitHub (como los bloqueos manuales) y las lee la corrida, la de la
pantalla y la programada de las 6:30.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime

import pandas as pd

from forusight.data import calendario as CAL
from forusight.data.corridas import LIMA

RUTA_BASE = "rutas/rutas_mall.csv"
RUTA_EXCEPCIONES = "rutas/excepciones.csv"
TODOS = "TODOS"
SIN_DESPACHO, DESPACHO_EXTRA = "sin_despacho", "despacho_extra"
ACCIONES = {SIN_DESPACHO: "No despacha", DESPACHO_EXTRA: "Despacha (extra)"}
COLS_BASE = ["mall", "patrones", "dias"]
COLS_EXC = ["fecha", "mall", "accion", "motivo", "usuario", "registrado"]


# ------------------------------------------------------------------ lectura / escritura


def base_por_defecto() -> pd.DataFrame:
    return CAL.rutas()[COLS_BASE].copy()


def leer_base(contenido: bytes | None) -> pd.DataFrame:
    if not contenido:
        return base_por_defecto()
    df = pd.read_csv(io.BytesIO(contenido), dtype=str, keep_default_na=False)
    return normalizar_base(df)


def excepciones_vacio() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS_EXC)


def leer_excepciones(contenido: bytes | None) -> pd.DataFrame:
    if not contenido:
        return excepciones_vacio()
    df = pd.read_csv(io.BytesIO(contenido), dtype=str, keep_default_na=False)
    for c in COLS_EXC:
        if c not in df:
            df[c] = ""
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.normalize()
    return df.dropna(subset=["fecha"])[COLS_EXC].reset_index(drop=True)


def a_csv(df: pd.DataFrame) -> bytes:
    out = df.copy()
    if "fecha" in out:
        out["fecha"] = pd.to_datetime(out["fecha"]).dt.strftime("%Y-%m-%d")
    return out.to_csv(index=False).encode("utf-8-sig")


def huella(base: pd.DataFrame, excepciones: pd.DataFrame) -> str:
    """Cambia si cambia una ruta o una excepción: invalida la caché de la corrida."""
    return hashlib.sha1(a_csv(base) + a_csv(excepciones)).hexdigest()[:10]


# ------------------------------------------------------------------ validación


def normalizar_dias(texto) -> str:
    """'lu, mi,VI' → 'LU,MI,VI' en el orden de la semana; '' si no hay días."""
    partes = {p.strip().upper()[:2] for p in str(texto or "").replace(";", ",").split(",")}
    return ",".join(d for d in CAL.DIAS if d in partes)


def normalizar_base(df: pd.DataFrame) -> pd.DataFrame:
    """Ruta semanal limpia; lanza ValueError con un texto claro si algo no cuadra."""
    for c in COLS_BASE:
        if c not in df:
            df[c] = ""
    out = df[COLS_BASE].fillna("").astype(str).apply(lambda s: s.str.strip())
    out = out.loc[out["mall"].ne("")].copy()
    out["patrones"] = out["patrones"].str.upper().str.replace(r"\s*\|\s*", "|", regex=True)
    out["dias"] = out["dias"].map(normalizar_dias)
    errores = []
    dup = out.loc[out["mall"].str.upper().duplicated(), "mall"].tolist()
    if dup:
        errores.append(f"mall repetido: {', '.join(dup)}")
    sin_patron = out.loc[out["patrones"].eq(""), "mall"].tolist()
    if sin_patron:
        errores.append(f"sin texto para reconocer sus tiendas: {', '.join(sin_patron)}")
    if errores:
        raise ValueError("Revisa la ruta semanal — " + "; ".join(errores) + ".")
    return out.reset_index(drop=True)


def nuevas_excepciones(
    fechas: list, malls: list[str], accion: str, motivo: str, usuario: str
) -> pd.DataFrame:
    if accion not in ACCIONES:
        raise ValueError(f"Acción desconocida: {accion}")
    if not fechas or not malls:
        raise ValueError("Elige al menos una fecha y un mall.")
    ahora = datetime.now(LIMA).strftime("%Y-%m-%d %H:%M")
    filas = [
        {
            "fecha": pd.Timestamp(f).normalize(),
            "mall": m,
            "accion": accion,
            "motivo": (motivo or "").strip(),
            "usuario": usuario,
            "registrado": ahora,
        }
        for f in fechas
        for m in malls
    ]
    return pd.DataFrame(filas, columns=COLS_EXC)


def mover_despacho(desde, hacia, malls: list[str], motivo: str, usuario: str) -> pd.DataFrame:
    """Feriado: el despacho de ``desde`` sale ``hacia`` (dos excepciones)."""
    if pd.Timestamp(desde).normalize() == pd.Timestamp(hacia).normalize():
        raise ValueError("La nueva fecha tiene que ser distinta de la original.")
    motivo = (motivo or "").strip()
    return pd.concat(
        [
            nuevas_excepciones(
                [desde],
                malls,
                SIN_DESPACHO,
                motivo or f"Se mueve al {pd.Timestamp(hacia):%d/%m}",
                usuario,
            ),
            nuevas_excepciones(
                [hacia],
                malls,
                DESPACHO_EXTRA,
                motivo or f"Viene del {pd.Timestamp(desde):%d/%m}",
                usuario,
            ),
        ],
        ignore_index=True,
    )


def agregar(excepciones: pd.DataFrame, nuevas: pd.DataFrame) -> pd.DataFrame:
    """La última excepción de una fecha × mall reemplaza a la anterior."""
    todo = pd.concat([excepciones, nuevas], ignore_index=True)
    todo["fecha"] = pd.to_datetime(todo["fecha"]).dt.normalize()
    todo = todo.drop_duplicates(["fecha", "mall"], keep="last")
    return todo.sort_values(["fecha", "mall"]).reset_index(drop=True)[COLS_EXC]


# ------------------------------------------------------------------ consulta


def despacha(mall: str, fecha, base: pd.DataFrame, excepciones: pd.DataFrame | None) -> bool:
    """¿Sale el camión a ese mall ese día? Excepción del mall > excepción TODOS > ruta semanal."""
    dia = pd.Timestamp(fecha).normalize()
    if excepciones is not None and len(excepciones):
        e = excepciones.loc[pd.to_datetime(excepciones["fecha"]).dt.normalize().eq(dia)]
        for quien in (mall, TODOS):
            x = e.loc[e["mall"].astype(str).str.upper().eq(str(quien).upper()), "accion"]
            if len(x):
                return x.iloc[-1] == DESPACHO_EXTRA
    dias = dias_de(mall, base)
    return CAL.dia_semana(dia) in dias.split(",") if dias else False


def dias_de(mall: str, base: pd.DataFrame) -> str:
    fila = base.loc[base["mall"].astype(str).str.upper().eq(str(mall).upper()), "dias"]
    return str(fila.iloc[0]) if len(fila) else ""


def excepcion_de(mall: str, fecha, excepciones: pd.DataFrame | None) -> pd.Series | None:
    if excepciones is None or not len(excepciones):
        return None
    dia = pd.Timestamp(fecha).normalize()
    e = excepciones.loc[pd.to_datetime(excepciones["fecha"]).dt.normalize().eq(dia)]
    for quien in (mall, TODOS):
        x = e.loc[e["mall"].astype(str).str.upper().eq(str(quien).upper())]
        if len(x):
            return x.iloc[-1]
    return None


def semana(base: pd.DataFrame, excepciones: pd.DataFrame, desde, dias: int = 14) -> pd.DataFrame:
    """Mall × fecha: '✓' despacha, '✓ extra', '✗ feriado/imprevisto', '' no toca."""
    fechas = pd.date_range(pd.Timestamp(desde).normalize(), periods=dias, freq="D")
    filas = []
    for mall in base["mall"]:
        fila = {"Mall": mall}
        for f in fechas:
            toca = CAL.dia_semana(f) in dias_de(mall, base).split(",")
            sale = despacha(mall, f, base, excepciones)
            fila[f"{CAL.dia_semana(f)} {f:%d/%m}"] = (
                ("✓" if toca else "✓ extra") if sale else ("✗ excepción" if toca else "")
            )
        filas.append(fila)
    return pd.DataFrame(filas)


def tiendas_por_mall(base: pd.DataFrame) -> pd.DataFrame:
    """codigo_tienda, nombre_tienda, mall: el del calendario de tiendas o, si no tiene, el que
    se reconoce en su centro comercial, zona o nombre (como la corrida)."""
    from forusight.data import cadenas as CAD

    cat = CAD.catalogo_tiendas().copy()
    cal = CAL.calendario().drop_duplicates("codigo_tienda").set_index("codigo_tienda")["mall"]
    del_cal = cat["codigo_tienda"].map(cal).fillna("").astype(str)
    cols = [cat.get(c, pd.Series("", index=cat.index)) for c in ("centro_comercial", "zona")]
    cat["mall"] = [
        m or CAL.mall_de(cc, z, n, tabla=base)
        for m, cc, z, n in zip(del_cal, cols[0], cols[1], cat["nombre_tienda"], strict=True)
    ]
    return cat[["codigo_tienda", "nombre_tienda", "mall"]]


# ------------------------------------------------------------------ una semana en un cuadro
#
# La página Rutas muestra una semana con fechas: mall × día, marcado si sale el camión. El
# cuadro es la verdad de esa semana: lo que difiere de la ruta fija se guarda como excepción
# de esa fecha (desmarcar = no despacha, marcar fuera de ruta = despacho extra) y lo que
# coincide no deja excepción. Un feriado es «TODOS no despacha» en esa fecha.

MOTIVO_FERIADO = "Feriado"


def fechas_semana(fecha) -> list[pd.Timestamp]:
    """Lunes a domingo de la semana de ``fecha``."""
    d = pd.Timestamp(fecha).normalize()
    lunes = d - pd.Timedelta(days=d.weekday())
    return [lunes + pd.Timedelta(days=i) for i in range(7)]


def etiqueta(fecha) -> str:
    return f"{CAL.dia_semana(fecha)} {pd.Timestamp(fecha):%d/%m}"


def _de_las_fechas(excepciones: pd.DataFrame, fechas) -> pd.Series:
    dias = {pd.Timestamp(f).normalize() for f in fechas}
    return pd.to_datetime(excepciones["fecha"]).dt.normalize().isin(dias)


def feriados(excepciones: pd.DataFrame, fechas) -> list[pd.Timestamp]:
    """Fechas (de ``fechas``) con «TODOS no despacha»."""
    if excepciones is None or excepciones.empty:
        return []
    e = excepciones.loc[
        _de_las_fechas(excepciones, fechas)
        & excepciones["mall"].astype(str).str.upper().eq(TODOS)
        & excepciones["accion"].eq(SIN_DESPACHO)
    ]
    return sorted(pd.to_datetime(e["fecha"]).dt.normalize().unique())


def marcar_feriados(excepciones: pd.DataFrame, fechas, elegidos, usuario: str) -> pd.DataFrame:
    """Los feriados de la semana pasan a ser exactamente ``elegidos`` (TODOS no despacha)."""
    elegidos = {pd.Timestamp(f).normalize() for f in elegidos}
    todos = excepciones["mall"].astype(str).str.upper().eq(TODOS)
    resto = excepciones.loc[~(todos & _de_las_fechas(excepciones, fechas))]
    ya = set(feriados(excepciones, fechas))
    nuevas = [
        nuevas_excepciones([f], [TODOS], SIN_DESPACHO, MOTIVO_FERIADO, usuario)
        for f in sorted(elegidos - ya)
    ]
    se_quedan = excepciones.loc[todos & _de_las_fechas(excepciones, sorted(elegidos & ya))]
    return agregar(resto, pd.concat([se_quedan, *nuevas], ignore_index=True))


def tabla_semana(base: pd.DataFrame, excepciones: pd.DataFrame, fecha) -> pd.DataFrame:
    """Mall + una columna por día (``LU 12/10``): True si ese día sale el camión."""
    fechas = fechas_semana(fecha)
    return pd.DataFrame(
        {
            "Mall": base["mall"].tolist(),
            **{
                etiqueta(f): [despacha(m, f, base, excepciones) for m in base["mall"]]
                for f in fechas
            },
        }
    )


def aplicar_semana(
    base: pd.DataFrame, excepciones: pd.DataFrame, fecha, tabla: pd.DataFrame, usuario: str
) -> pd.DataFrame:
    """Excepciones por mall de la semana a partir del cuadro editado. Las que ya estaban y
    siguen valiendo se conservan tal cual (con su motivo y usuario)."""
    fechas = fechas_semana(fecha)
    fer = set(feriados(excepciones, fechas))
    por_mall = ~excepciones["mall"].astype(str).str.upper().eq(TODOS)
    semana_mall = excepciones.loc[por_mall & _de_las_fechas(excepciones, fechas)]
    resto = excepciones.loc[~(por_mall & _de_las_fechas(excepciones, fechas))]
    previas = {
        (pd.Timestamp(r.fecha).normalize(), str(r.mall).upper(), r.accion): r
        for r in semana_mall.itertuples(index=False)
    }
    filas = []
    for _, fila in tabla.iterrows():
        mall = str(fila["Mall"])
        for f in fechas:
            sale = bool(fila[etiqueta(f)])
            por_ruta = f not in fer and CAL.dia_semana(f) in dias_de(mall, base).split(",")
            if sale == por_ruta:
                continue
            accion = DESPACHO_EXTRA if sale else SIN_DESPACHO
            previa = previas.get((f, mall.upper(), accion))
            if previa is not None:
                filas.append(pd.DataFrame([previa._asdict()], columns=COLS_EXC))
                continue
            motivo = "Por feriado" if fer else "Cambio de ruta"
            filas.append(nuevas_excepciones([f], [mall], accion, motivo, usuario))
    return agregar(resto, pd.concat(filas, ignore_index=True) if filas else excepciones_vacio())


def cambios_semana(base: pd.DataFrame, excepciones: pd.DataFrame, fecha) -> list[str]:
    """En palabras, qué cambia esta semana respecto de la ruta fija."""
    fechas = fechas_semana(fecha)
    out = [f"{etiqueta(f)}: feriado, no sale el camión" for f in feriados(excepciones, fechas)]
    for mall in base["mall"]:
        extra = [
            etiqueta(f)
            for f in fechas
            if despacha(mall, f, base, excepciones)
            and CAL.dia_semana(f) not in dias_de(mall, base).split(",")
        ]
        quita = [
            etiqueta(f)
            for f in fechas
            if not despacha(mall, f, base, excepciones)
            and CAL.dia_semana(f) in dias_de(mall, base).split(",")
            and f not in feriados(excepciones, [f])
        ]
        if extra or quita:
            partes = ([f"sale {', '.join(extra)}"] if extra else []) + (
                [f"no sale {', '.join(quita)}"] if quita else []
            )
            out.append(f"{mall}: {' · '.join(partes)}")
    return out
