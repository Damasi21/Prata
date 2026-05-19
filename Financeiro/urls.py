from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('contas-bancarias/', views.contas_bancarias, name='contas_bancarias'),
    path('categorias/', views.categorias, name='categorias'),
    path('eventos/', views.eventos, name='eventos'),
    path('importar/ofx/', views.importar_ofx, name='importar_ofx'),
    path('lancamentos/<int:lancamento_id>/excluir/', views.excluir_lancamento, name='excluir_lancamento'),
    path('importar/ofx/preview/', views.categorizar_ofx_preview, name='categorizar_ofx_preview'),
    path('importar/ofx/<int:importacao_id>/categorizar/', views.categorizar_importacao, name='categorizar_importacao'),
    path('importar/excel/', views.importar_excel, name='importar_excel'),
    path('budgets/', views.budgets, name='budgets'),
    path('contas-pagar-receber/', views.contas_pagar_receber, name='contas_pagar_receber'),
    path('fluxo-de-caixa/', views.fluxo_de_caixa, name='fluxo_de_caixa'),
    path('relatorios-bi/', views.relatorios_bi, name='relatorios_bi'),
]
