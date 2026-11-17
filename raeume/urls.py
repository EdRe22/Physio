from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="raeume_index"),
    path("buchen/", views.book, name="raeume_book"),
    path("frei/", views.free, name="raeume_free"),
    path("verwalten/", views.manage, name="raeume_manage"),
    path("<int:pk>/stornieren/", views.cancel, name="raeume_cancel"),
    path("<int:pk>/umschalten/", views.toggle, name="raeume_toggle"),
]
