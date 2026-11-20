"""Forget the admin password set in the dashboard; FARM_ADMIN_KEY from .env applies again.

    docker compose exec farm-server python -m server.farm_server.reset_admin_password
"""
from .database import connection, initialize
from .main import ADMIN_PASSWORD_SETTING

initialize()
with connection() as db:
    removed = db.execute("DELETE FROM app_settings WHERE key=?", (ADMIN_PASSWORD_SETTING,)).rowcount
print("Heslo z dashboardu smazáno, platí opět FARM_ADMIN_KEY z .env." if removed
      else "Heslo z dashboardu nebylo nastavené, platí FARM_ADMIN_KEY z .env.")
