import sys

from django.apps import AppConfig
from django.db.backends.signals import connection_created


def configurar_sqlite_local(sender, connection, **kwargs):
    if connection.vendor != 'sqlite' or 'test' in sys.argv:
        return

    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=OFF')
        cursor.execute('PRAGMA synchronous=OFF')


class FinanceiroConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Financeiro'

    def ready(self):
        connection_created.connect(configurar_sqlite_local, dispatch_uid='financeiro.configurar_sqlite_local')
