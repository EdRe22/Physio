from django.urls import path

from . import views

urlpatterns = [
    path("", views.invoice_list, name="invoice_list"),
    path("leistungen/", views.leistung_list, name="leistung_list"),
    path("leistungen/neu/", views.leistung_edit, name="leistung_create"),
    path("leistungen/<int:pk>/", views.leistung_edit, name="leistung_edit"),
    path("ueberfaellig/", views.overdue_list, name="overdue_list"),
    path("neu/<int:patient_pk>/", views.invoice_new, name="invoice_new"),
    path("<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("<int:pk>/position/", views.position_add, name="position_add"),
    path("<int:pk>/position/<int:position_pk>/loeschen/", views.position_delete, name="position_delete"),
    path("<int:pk>/ausstellen/", views.invoice_issue, name="invoice_issue"),
    path("<int:pk>/loeschen/", views.invoice_delete, name="invoice_delete"),
    path("<int:pk>/storno/", views.invoice_storno, name="invoice_storno"),
    path("<int:pk>/zahlung/", views.payment_add, name="payment_add"),
    path("<int:pk>/mahnung/", views.reminder_add, name="reminder_add"),
    path("mahnung/<int:pk>/pdf/", views.reminder_pdf, name="reminder_pdf"),
    path("mahnung/<int:pk>/mail/", views.reminder_mail, name="reminder_mail"),
    path("<int:pk>/mail/", views.invoice_mail, name="invoice_mail"),
]
