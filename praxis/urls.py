from django.urls import path

from . import views

urlpatterns = [
    path("", views.rent_list, name="rent_list"),
    path("profil/", views.profile_edit, name="rent_profile"),
    path("profil/logo/", views.profile_logo, name="rent_profile_logo"),
    path("mietvertraege/", views.lease_list, name="lease_list"),
    path("ausstellen/", views.issue_month, name="rent_issue"),
    path("<int:pk>/", views.rent_detail, name="rent_detail"),
    path("<int:pk>/pdf/", views.rent_pdf, name="rent_pdf"),
    path("<int:pk>/bezahlt/", views.rent_paid, name="rent_paid"),
    path("<int:pk>/buchen/", views.rent_book, name="rent_book"),
    path("<int:pk>/storno/", views.rent_storno, name="rent_storno"),
]
