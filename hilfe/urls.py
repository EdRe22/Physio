from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="hilfe_index"),
    path("<slug:slug>/", views.topic, name="hilfe_topic"),
]
