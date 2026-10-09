"""Descargas: el Excel de cada corrida automática de las 6:30, sólo de la semana en curso."""

from app.components.corridas_guardadas import pagina
from app.components.ui import hero

hero(
    "Descargas",
    "Todos los días a las 6:30 la distribución se corre sola y su Excel queda aquí. Se guardan "
    "las de esta semana (lunes a domingo); el lunes se borran las de la semana anterior.",
    eyebrow="Corridas automáticas",
)
pagina()
