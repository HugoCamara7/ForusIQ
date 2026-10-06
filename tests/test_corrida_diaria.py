"""Corrida diaria programada (6:30 Lima): nombres, decisión, idempotencia y el Excel real.

Las pruebas EJECUTAN la corrida con datos sintéticos y un repositorio de datos falso: leer
el script no dice si el Excel llega a guardarse.
"""

import ast
import importlib.util
import io
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from forusight.data import corridas as C

RAIZ = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location(
        "corrida_diaria", RAIZ / "scripts" / "corrida_diaria.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["corrida_diaria"] = mod
    spec.loader.exec_module(mod)
    return mod


class StoreFalso:
    """Mismo contrato que GitHubStore: guardar(ruta) escribe en prefix/ruta."""

    prefix = "forusight"

    def __init__(self):
        self.archivos: dict[str, bytes] = {}

    def guardar(self, ruta, contenido, mensaje):
        path = f"{self.prefix}/{ruta}"
        self.archivos[path] = contenido
        return path

    def listar(self, carpeta):
        base = f"{self.prefix}/{carpeta.strip('/')}/"
        return [
            {"name": p[len(base) :], "path": p, "sha": str(len(b)), "type": "file"}
            for p, b in self.archivos.items()
            if p.startswith(base) and "/" not in p[len(base) :]
        ]

    def leer(self, path):
        return self.archivos.get(path)


def lima(h, m=0):
    return datetime(2026, 10, 5, h, m, tzinfo=C.LIMA)


# ------------------------------------------------------------------ nombres y carpeta


def test_nombre_y_ruta_con_la_fecha_en_iso():
    assert C.nombre_diario("2026-10-05") == "2026-10-05_Distribucion_Forusight.xlsx"
    assert C.ruta_diaria("2026-10-05") == "corridas/2026-10/2026-10-05_Distribucion_Forusight.xlsx"
    assert C.nombre_diario("2026-10-05", preliminar=True).endswith("_PRELIMINAR.xlsx")


def test_leer_nombre_ida_y_vuelta():
    for pre in (False, True):
        f, p = C.leer_nombre(C.nombre_diario("2026-10-05", pre))
        assert f == pd.Timestamp("2026-10-05") and p is pre
    assert C.leer_nombre("2026-10-05_10-00_ERROR.txt") is None
    assert C.leer_nombre("otra_cosa.xlsx") is None


def test_una_preliminar_no_cuenta_como_guardada():
    pre = [{"name": C.nombre_diario("2026-10-05", True)}]
    assert not C.ya_guardada(pre, "2026-10-05")
    assert C.ya_guardada(pre + [{"name": C.nombre_diario("2026-10-05")}], "2026-10-05")
    assert not C.ya_guardada([{"name": C.nombre_diario("2026-10-04")}], "2026-10-05")


def test_listado_del_mas_reciente_al_mas_antiguo_y_definitiva_primero():
    nombres = [
        C.nombre_diario("2026-10-03"),
        C.nombre_diario("2026-10-05", True),
        C.nombre_diario("2026-10-05"),
        "2026-10-05_0630_ERROR.txt",
    ]
    lista = C.corridas_listadas([{"name": n} for n in nombres])
    assert [(f"{a['fecha']:%d}", a["preliminar"]) for a in lista] == [
        ("05", False),
        ("05", True),
        ("03", False),
    ]


# ------------------------------------------------------------------ decisión


def test_decidir():
    S = _script()
    aviso = "La carga diaria aún no trae el cierre del 04/10"
    assert S.decidir(None, lima(6, 30)) == S.GUARDAR
    assert S.decidir(aviso, lima(6, 30)) == S.ESPERAR
    assert S.decidir(aviso, lima(7, 30)) == S.ESPERAR
    assert S.decidir(aviso, lima(8, 0)) == S.PRELIMINAR
    assert S.decidir(aviso, lima(9, 40)) == S.PRELIMINAR  # cron atrasado: sigue siendo el último
    assert S.decidir(aviso, lima(6, 30), forzar=True) == S.PRELIMINAR


def test_el_cron_son_las_630_de_lima_y_el_ultimo_intento_coincide():
    import yaml

    wf = yaml.safe_load((RAIZ / ".github/workflows/corrida-diaria.yml").read_text(encoding="utf-8"))
    crons = [c["cron"] for c in wf[True]["schedule"]]  # «on» lo lee yaml como True
    horas_lima = sorted(((int(c.split()[1]) - 5) * 60 + int(c.split()[0])) for c in crons)
    assert horas_lima[0] == 6 * 60 + 30
    assert horas_lima[-1] == _script().MINUTO_ULTIMO_INTENTO
    texto = (RAIZ / ".github/workflows/corrida-diaria.yml").read_text(encoding="utf-8")
    assert "upload-artifact" not in texto  # el repo es público: sería publicar el Excel


def test_el_script_solo_imprime_en_main():
    """El log es público: ningún print fuera de main()."""
    arbol = ast.parse((RAIZ / "scripts" / "corrida_diaria.py").read_text(encoding="utf-8"))
    for f in arbol.body:
        if isinstance(f, ast.FunctionDef) and f.name != "main":
            prints = [
                n
                for n in ast.walk(f)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"
            ]
            assert not prints, f.name


# ------------------------------------------------------------------ la corrida de verdad


@pytest.fixture
def sintetico(monkeypatch):
    monkeypatch.setenv("FORUSIGHT_DATA_SOURCE", "synthetic")
    monkeypatch.chdir(RAIZ)


def test_guarda_el_excel_con_la_fecha_y_es_el_de_la_pantalla(sintetico):
    S = _script()
    store = StoreFalso()
    r = S.correr(lima(6, 31), store, "synthetic")
    assert r["estado"] == "guardada", r
    ruta = "forusight/corridas/2026-10/2026-10-05_Distribucion_Forusight.xlsx"
    assert r["ruta"] == ruta and ruta in store.archivos
    hojas = pd.read_excel(io.BytesIO(store.archivos[ruta]), sheet_name=None)
    assert len(hojas) >= 2 and r["unidades"] > 0


def test_el_segundo_intento_no_vuelve_a_correr(sintetico):
    S = _script()
    store = StoreFalso()
    S.correr(lima(6, 31), store, "synthetic")
    antes = dict(store.archivos)
    r = S.correr(lima(7, 2), store, "synthetic")
    assert r["estado"] == "ya estaba guardada" and store.archivos == antes


def test_sin_la_carga_diaria_espera_y_al_final_guarda_preliminar(sintetico, monkeypatch):
    from app.components import estado as E

    S = _script()
    real = E.correr_motor

    def sin_cierre(*a, **k):
        res, diag = real(*a, **k)
        return res, {**diag, "aviso_carga": "La carga diaria aún no trae el cierre del 04/10"}

    monkeypatch.setattr(E, "correr_motor", sin_cierre)
    store = StoreFalso()
    assert S.correr(lima(6, 31), store, "synthetic")["estado"].startswith("carga diaria pendiente")
    assert not store.archivos
    r = S.correr(lima(8, 3), store, "synthetic")
    assert "PRELIMINAR" in r["estado"] and r["ruta"].endswith("_PRELIMINAR.xlsx")
    # la preliminar no bloquea a la buena si la carga llega después
    monkeypatch.setattr(E, "correr_motor", real)
    assert S.correr(lima(9, 0), store, "synthetic")["estado"] == "guardada"


def test_marcas_por_defecto_es_la_regla_de_la_barra_lateral():
    from app.components.estado import marcas_por_defecto

    ops = ["AZALEIA", "HUSH PUPPIES", "VANS"]
    assert marcas_por_defecto(ops, ["hush puppies"]) == ["HUSH PUPPIES"]
    assert marcas_por_defecto(["AZALEIA KIDS"], ["AZALEIA"]) == ["AZALEIA KIDS"]
    assert marcas_por_defecto(ops, ["NADA"]) == []


# ------------------------------------------------------------------ el login


def test_el_login_no_anuncia_la_lectura_de_arti():
    estado = (RAIZ / "app/components/estado.py").read_text(encoding="utf-8")
    login = (RAIZ / "app/components/login.py").read_text(encoding="utf-8")
    arbol = ast.parse(estado)
    f = next(n for n in arbol.body if isinstance(n, ast.FunctionDef) and n.name == "_marcas_arti")
    spinner = [
        k.value.value
        for d in f.decorator_list
        if isinstance(d, ast.Call)
        for k in d.keywords
        if k.arg == "show_spinner"
    ]
    assert spinner == [False]
    assert "Ingresando…" in login


def test_de_noche_en_lima_la_app_no_vive_en_manana(monkeypatch):
    """El servidor corre en UTC: el lunes 05/10 a las 20:00 de Lima ya es martes 06/10 01:00
    UTC. La app pedía el cierre del 05/10 (llega a las 3:00) y armaba la ruta del martes.
    Con la hora de Lima el «hoy» sigue siendo el lunes y el stock al 04/10 está al día."""
    from app.components import estado as E

    noche = datetime(2026, 10, 5, 20, 0, tzinfo=C.LIMA)
    assert noche.astimezone(C.timezone.utc).date().isoformat() == "2026-10-06"
    monkeypatch.setattr(C, "ahora_lima", lambda: noche)
    assert C.hoy_lima() == pd.Timestamp("2026-10-05")
    assert E.lunes_actual() == pd.Timestamp("2026-10-05")
    al_dia = {"fecha_foto": "2026-10-04", "venta_hasta": "2026-10-04"}
    assert E.aviso_carga(al_dia) is None

    # El martes a las 7:00 sin la carga de las 3:00: ahí sí falta el cierre del 05/10.
    monkeypatch.setattr(C, "ahora_lima", lambda: datetime(2026, 10, 6, 7, 0, tzinfo=C.LIMA))
    assert "05/10" in E.aviso_carga(al_dia)
