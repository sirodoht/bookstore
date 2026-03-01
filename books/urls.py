from django.urls import path

from . import views

app_name = "books"

urlpatterns = [
    path("", views.BookListView.as_view(), name="book-list"),
    path("new/", views.BookCreateView.as_view(), name="book-create"),
    path("book/<int:pk>/edit/", views.BookUpdateView.as_view(), name="book-update"),
    path("new-batch/", views.BookBatchUploadView.as_view(), name="batch-upload"),
    path(
        "new-batch/stream/",
        views.batch_upload_stream,
        name="batch-upload-stream",
    ),
    path("batch-results/", views.batch_results, name="batch-results"),
    path("book/<int:pk>/buy/", views.BookPurchaseView.as_view(), name="book-buy"),
    path(
        "checkout/success/",
        views.CheckoutSuccessView.as_view(),
        name="checkout-success",
    ),
    path(
        "checkout/cancel/", views.CheckoutCancelView.as_view(), name="checkout-cancel"
    ),
    path("stripe/webhook/", views.stripe_webhook, name="stripe-webhook"),
    path("analyze-cover/", views.analyze_cover, name="analyze-cover"),
]
