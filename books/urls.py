from django.urls import path

from . import views

app_name = "books"

urlpatterns = [
    path("", views.BookListView.as_view(), name="book-list"),
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
]
