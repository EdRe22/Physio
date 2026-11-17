from django.urls import path

from . import views

urlpatterns = [
    path("", views.journal, name="journal"),
    path("neu/", views.buchung_new, name="buchung_new"),
    path("buchung/<int:pk>/", views.buchung_detail, name="buchung_detail"),
    path("buchung/<int:pk>/storno/", views.buchung_reverse, name="buchung_reverse"),
    path("buchung/<int:pk>/beleg/", views.beleg_attach, name="beleg_attach"),
    path("beleg/<int:pk>/", views.beleg_download, name="beleg_download"),
    path("bank/", views.bank_list, name="bank_list"),
    path("bank/sichere-uebernehmen/", views.bank_accept_all, name="bank_accept_all"),
    path("bank/<int:pk>/zuordnen/", views.bank_accept, name="bank_accept"),
    path("bank/<int:pk>/buchen/", views.bank_book, name="bank_book"),
    path("bank/<int:pk>/ignorieren/", views.bank_ignore, name="bank_ignore"),
    path("auswertung/", views.report, name="report"),
    path("export/", views.export, name="export"),
]
