from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("data-format", views.data_format, name="data-format"),
    path("view", views.view_data, name="view-data"),
    path("download-all", views.download_all, name="download-all"),
    path("download-results", views.download_results, name="download-results"),
    path("search", views.search, name="search"),
    path("upload", views.upload, name="upload-obs"),
    path("download-ids", views.download_obs_ids, name="download-obs-ids"),
    path("health", views.health, name="health"),
    path("about", views.about, name="about"),
]
