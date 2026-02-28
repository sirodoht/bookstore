"""Django views for books app."""

import stripe
from django.conf import settings
from django.core.mail import send_mail
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import ListView, TemplateView

from .models import Book, Order

stripe.api_key = settings.STRIPE_SECRET_KEY


class BookListView(ListView):
    """List all available books."""

    model = Book
    template_name = "books/book_list.html"
    context_object_name = "books"

    def get_queryset(self):
        return Book.objects.filter(is_available=True)


class BookPurchaseView(View):
    """Handle book purchase and redirect to Stripe Checkout."""

    def post(self, request, pk):
        book = get_object_or_404(Book, pk=pk, is_available=True)

        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": "gbp",
                            "product_data": {
                                "name": book.title,
                                "description": f"by {book.author}",
                            },
                            "unit_amount": int(book.price * 100),
                        },
                        "quantity": 1,
                    }
                ],
                mode="payment",
                success_url=request.build_absolute_uri("/checkout/success/")
                + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=request.build_absolute_uri("/checkout/cancel/"),
                metadata={"book_id": book.id},
                shipping_address_collection={
                    "allowed_countries": ["GB"],
                },
            )
            return redirect(session.url)
        except Exception:
            return redirect("books:book-list")


class CheckoutSuccessView(TemplateView):
    """Display after successful checkout."""

    template_name = "books/checkout_success.html"


class CheckoutCancelView(TemplateView):
    """Display after cancelled checkout."""

    template_name = "books/checkout_cancel.html"


@csrf_exempt
def stripe_webhook(request):
    """Handle Stripe webhook events."""
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        return HttpResponse(status=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        book_id = session["metadata"]["book_id"]
        customer_email = session["customer_details"]["email"]

        book = Book.objects.get(id=book_id)
        book.is_available = False
        book.save()

        # Create order record with book snapshot
        shipping_details = session.get("shipping_details", {})
        address = shipping_details.get("address", {})

        order = Order.objects.create(
            book_title=book.title,
            book_author=book.author,
            book_isbn=book.isbn,
            book_price=book.price,
            stripe_session_id=session["id"],
            customer_email=customer_email,
            amount_paid=session["amount_total"] / 100,
            shipping_name=shipping_details.get("name", ""),
            shipping_address_line1=address.get("line1", ""),
            shipping_address_line2=address.get("line2", ""),
            shipping_city=address.get("city", ""),
            shipping_state=address.get("state", ""),
            shipping_postal_code=address.get("postal_code", ""),
            shipping_country=address.get("country", ""),
        )

        send_purchase_confirmation(order)
        send_admin_notification(order)

    return JsonResponse({"status": "success"})


def send_purchase_confirmation(order):
    """Send confirmation email to customer."""
    purchase_date = order.created_at.strftime("%Y-%m-%d %H:%M:%S")

    shipping_info = ""
    if order.shipping_address_line1:
        shipping_info = f"""

SHIPPING ADDRESS
----------------
Name: {order.shipping_name}
Address: {order.shipping_address_line1}
"""
        if order.shipping_address_line2:
            shipping_info += f"           {order.shipping_address_line2}\n"
        shipping_info += f"""City: {order.shipping_city}
State/Province: {order.shipping_state}
ZIP/Postal Code: {order.shipping_postal_code}
Country: {order.shipping_country}"""

    subject = f"[bookstore] Order Confirmation #{order.id} - {order.book_title}"
    body = f"""Thank you for your purchase!

ORDER #{order.id}
----------------
Order Date: {purchase_date}
Status: Pending (we'll notify you when shipped)

BOOK DETAILS
----------------
Title: {order.book_title}
Author: {order.book_author}
ISBN: {order.book_isbn}
Price: £{order.amount_paid:.2f}
{shipping_info}

If you have any questions about your order, please contact us and reference Order #{order.id}.
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [order.customer_email],
        fail_silently=False,
    )


def send_admin_notification(order):
    """Send notification email to admin about new order."""
    if not settings.ADMINS:
        return

    purchase_date = order.created_at.strftime("%Y-%m-%d %H:%M:%S")

    shipping_info = ""
    if order.shipping_address_line1:
        shipping_info = f"""
SHIPPING ADDRESS:
Name: {order.shipping_name}
Address: {order.shipping_address_line1}
"""
        if order.shipping_address_line2:
            shipping_info += f"         {order.shipping_address_line2}\n"
        shipping_info += f"""City: {order.shipping_city}
State: {order.shipping_state}
Postcode: {order.shipping_postal_code}
Country: {order.shipping_country}"""

    subject = f"[bookstore] New Order #{order.id} - {order.book_title}"
    body = f"""A new order has been placed!

ORDER #{order.id}
----------------
Order Date: {purchase_date}
Customer Email: {order.customer_email}
Stripe Session: {order.stripe_session_id}

BOOK DETAILS:
Title: {order.book_title}
Author: {order.book_author}
ISBN: {order.book_isbn}
Price: £{order.amount_paid:.2f}
{shipping_info}

Please fulfill this order at your earliest convenience.
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        settings.ADMINS,
        fail_silently=False,
    )
