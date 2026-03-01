"""Django views for books app."""

import base64
import json
import logging
from decimal import Decimal

import openai
import stripe
from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, ListView, TemplateView

from .models import Book, Order

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY
openai.api_key = settings.OPENAI_API_KEY


class BookListView(ListView):
    """List all available books."""

    model = Book
    template_name = "books/book_list.html"
    context_object_name = "books"

    def get_queryset(self):
        return Book.objects.filter(is_available=True)


class BookCreateView(UserPassesTestMixin, CreateView):
    """Create a new book (admin only)."""

    model = Book
    template_name = "books/book_form.html"
    fields = [
        "title",
        "author",
        "isbn",
        "description",
        "published_year",
        "price",
        "is_available",
        "cover_image",
    ]
    success_url = reverse_lazy("books:book-list")
    login_url = "/admin/login/"

    def test_func(self):
        """Only allow admin users."""
        return self.request.user.is_staff


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

    if not settings.STRIPE_WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET is not configured")
        return HttpResponse("Webhook secret not configured", status=500)

    if not sig_header:
        logger.warning("Missing Stripe signature header")
        return HttpResponse("Missing signature header", status=400)

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
        logger.info(
            "Received Stripe webhook event: %s (id: %s)", event["type"], event["id"]
        )
    except ValueError as e:
        logger.error("Invalid payload: %s", e)
        return HttpResponse("Invalid payload", status=400)
    except stripe.error.SignatureVerificationError as e:
        logger.error("Signature verification failed: %s", e)
        return HttpResponse("Invalid signature", status=400)
    except Exception as e:
        logger.error("Unexpected error during webhook construction: %s", e)
        return HttpResponse("Webhook processing error", status=500)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session.get("id", "unknown")

        try:
            metadata = session.get("metadata", {})
            book_id = metadata.get("book_id")
            customer_details = session.get("customer_details", {})
            customer_email = customer_details.get("email")

            if not book_id:
                logger.error(
                    "Missing book_id in session metadata (session: %s)", session_id
                )
                return HttpResponse("Missing book_id in metadata", status=400)

            if not customer_email:
                logger.error(
                    "Missing customer email in session (session: %s)", session_id
                )
                return HttpResponse("Missing customer email", status=400)

            # Idempotency check: skip if order already processed
            if Order.objects.filter(stripe_session_id=session_id).exists():
                logger.info(
                    "Order already processed for session %s, returning 200", session_id
                )
                return HttpResponse("Order already processed", status=200)

            shipping_details = session.get("shipping_details", {})
            address = shipping_details.get("address", {})
            amount_total = session.get("amount_total")

            if amount_total is None:
                logger.error(
                    "Missing amount_total in session (session: %s)", session_id
                )
                return HttpResponse("Missing amount_total", status=400)

            try:
                with transaction.atomic():
                    try:
                        book = Book.objects.select_for_update().get(id=book_id)
                    except Book.DoesNotExist:
                        logger.error(
                            "Book not found: %s (session: %s)", book_id, session_id
                        )
                        return HttpResponse(f"Book {book_id} not found", status=404)

                    if not book.is_available:
                        logger.warning(
                            "Book %s is already sold (session: %s)",
                            book_id,
                            session_id,
                        )
                        return HttpResponse("Book already sold", status=409)

                    # Price mismatch check: log warning but proceed with order
                    amount_paid = Decimal(amount_total) / Decimal(100)
                    if amount_paid != book.price:
                        logger.warning(
                            "Price mismatch for book %s (session: %s): "
                            "expected £%s, received £%s",
                            book_id,
                            session_id,
                            book.price,
                            amount_paid,
                        )

                    book.is_available = False
                    book.save()
                    logger.info("Marked book %s as unavailable", book_id)

                    order = Order.objects.create(
                        book_title=book.title,
                        book_author=book.author,
                        book_isbn=book.isbn,
                        book_price=book.price,
                        stripe_session_id=session_id,
                        customer_email=customer_email,
                        amount_paid=amount_paid,
                        shipping_name=shipping_details.get("name", ""),
                        shipping_address_line1=address.get("line1", ""),
                        shipping_address_line2=address.get("line2", ""),
                        shipping_city=address.get("city", ""),
                        shipping_state=address.get("state", ""),
                        shipping_postal_code=address.get("postal_code", ""),
                        shipping_country=address.get("country", ""),
                    )
                    logger.info("Created order %s for book %s", order.id, book_id)
            except IntegrityError:
                logger.info(
                    "IntegrityError: order for session %s already exists (race condition), returning 200",
                    session_id,
                )
                return HttpResponse("Order already processed", status=200)

                send_purchase_confirmation(order)
                logger.info("Sent purchase confirmation for order %s", order.id)

                send_admin_notification(order)
                logger.info("Sent admin notification for order %s", order.id)

            except Exception as e:
                logger.exception(
                    "Failed to process order (session: %s): %s", session_id, e
                )
                return HttpResponse("Order processing failed", status=500)

        except Exception as e:
            logger.exception(
                "Unexpected error processing checkout.session.completed (session: %s): %s",
                session_id,
                e,
            )
            return HttpResponse("Processing error", status=500)

    return JsonResponse({"status": "success"})


def send_purchase_confirmation(order):
    """Send confirmation email to customer."""
    purchase_date = order.created_at.strftime("%Y-%m-%d %H:%M:%S")

    shipping_info = ""
    if order.shipping_address_line1:
        shipping_info = f"""

SHIPPING ADDRESS
-----
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
-----
Order Date: {purchase_date}
Status: Pending (we'll notify you when shipped)

BOOK DETAILS
-----
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
-----
Order Date: {purchase_date}
Customer Email: {order.customer_email}
Stripe Session: {order.stripe_session_id}

BOOK DETAILS:
Title: {order.book_title}
Author: {order.book_author}
ISBN: {order.book_isbn}
Price: £{order.amount_paid:.2f}
{shipping_info}
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        settings.ADMINS,
        fail_silently=False,
    )


@user_passes_test(lambda u: u.is_staff)
def analyze_cover(request):
    """Analyze book cover image using OpenAI and extract book details."""
    if request.method != "POST":
        logger.warning(
            "analyze_cover: Received non-POST request from user %s", request.user
        )
        return JsonResponse({"error": "Only POST requests allowed"}, status=405)

    if "cover_image" not in request.FILES:
        logger.warning(
            "analyze_cover: No cover_image in request from user %s", request.user
        )
        return JsonResponse({"error": "No image provided"}, status=400)

    cover_image = request.FILES["cover_image"]

    logger.info(
        "analyze_cover: Processing image '%s' for user %s",
        cover_image.name,
        request.user,
    )

    try:
        image_data = cover_image.read()
        base64_image = base64.b64encode(image_data).decode("utf-8")

        logger.debug(
            "analyze_cover: Image encoded successfully (%d bytes)", len(image_data)
        )
    except Exception as e:
        logger.error("analyze_cover: Failed to read/encode image: %s", e, exc_info=True)
        return JsonResponse({"error": "Failed to process image"}, status=500)

    if not settings.OPENAI_API_KEY:
        logger.error("analyze_cover: OPENAI_API_KEY not configured")
        return JsonResponse({"error": "OpenAI not configured"}, status=500)

    prompt = """Analyze this book cover image and provide the following information:

1. Title: The main title of the book
2. Author: The author's name
3. Description: A one-sentence blurb or description of what the book is about
4. Published Year: The publication year

Return ONLY a JSON object with these exact keys:
- title (string, empty if not known)
- author (string, empty if not known)
- description (string, empty if not known)
- published_year (string, empty if not known)

If any field cannot be determined, use an empty string as the value.
Do not include markdown formatting, just the raw JSON."""

    try:
        logger.info(
            "analyze_cover: Sending request to OpenAI for user %s", request.user
        )

        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
            max_completion_tokens=500,
        )

        content = response.choices[0].message.content
        logger.debug("analyze_cover: OpenAI response: %s", content)

        try:
            data = json.loads(content)

            required_keys = ["title", "author", "description", "published_year"]
            for key in required_keys:
                if key not in data:
                    logger.warning(
                        "analyze_cover: Missing key '%s' in OpenAI response", key
                    )
                    data[key] = ""

            logger.info(
                "analyze_cover: Successfully extracted data for user %s - title: '%s', author: '%s'",
                request.user,
                data.get("title", "")[:50],
                data.get("author", "")[:50],
            )

            return JsonResponse(data)

        except json.JSONDecodeError as e:
            logger.error(
                "analyze_cover: Failed to parse OpenAI JSON response: %s\nContent: %s",
                e,
                content,
            )
            return JsonResponse(
                {
                    "title": "",
                    "author": "",
                    "isbn": "",
                    "description": "",
                    "published_year": "",
                    "error": "Failed to parse AI response",
                }
            )

    except openai.APIError as e:
        logger.error("analyze_cover: OpenAI API error: %s", e, exc_info=True)
        return JsonResponse({"error": "AI service error"}, status=502)
    except openai.RateLimitError as e:
        logger.error("analyze_cover: OpenAI rate limit exceeded: %s", e, exc_info=True)
        return JsonResponse({"error": "AI service rate limited"}, status=429)
    except Exception as e:
        logger.exception("analyze_cover: Unexpected error: %s", e)
        return JsonResponse({"error": "Analysis failed"}, status=500)
