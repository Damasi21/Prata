from django.contrib import admin

from .models import Budget, Categoria, ContaBancaria, ContaPagarReceber, Evento, Importacao, Lancamento, RateioLancamento, RecorrenciaConta


admin.site.register(ContaBancaria)
admin.site.register(Categoria)
admin.site.register(Evento)
admin.site.register(Importacao)
admin.site.register(Lancamento)
admin.site.register(RateioLancamento)
admin.site.register(Budget)
admin.site.register(RecorrenciaConta)
admin.site.register(ContaPagarReceber)
