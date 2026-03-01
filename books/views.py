"""Django views for books app."""

import base64
import json
import logging
from decimal import Decimal

import openai
import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.http import JsonResponse
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
                success_url=request.build_absolute_uri("/checkout/success/"),
                cancel_url=request.build_absolute_uri("/checkout/cancel/"),
                metadata={"book_id": book.id},
                shipping_address_collection={
                    "allowed_countries": ["GB"],
                },
            )
            return redirect(session.url)
        except Exception:
            logger.exception("Failed to create Stripe checkout session for book %s", pk)
            messages.error(
                request,
                "Something went wrong while initiating payment. Please try again.",
            )
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
        return JsonResponse(
            {"status": "error", "message": "Webhook secret not configured"},
            status=500,
        )

    if not sig_header:
        logger.warning("Missing Stripe signature header")
        return JsonResponse(
            {"status": "error", "message": "Missing signature header"},
            status=400,
        )

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
        logger.info(
            "Received Stripe webhook event: %s (id: %s)", event["type"], event["id"]
        )
    except ValueError as e:
        logger.error("Invalid payload: %s", e)
        return JsonResponse(
            {"status": "error", "message": "Invalid payload"},
            status=400,
        )
    except stripe.SignatureVerificationError as e:
        logger.error("Signature verification failed: %s", e)
        return JsonResponse(
            {"status": "error", "message": "Invalid signature"},
            status=400,
        )
    except Exception as e:
        logger.error("Unexpected error during webhook construction: %s", e)
        return JsonResponse(
            {"status": "error", "message": "Webhook processing error"},
            status=500,
        )

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
                # Return 200 to acknowledge - missing metadata is a permanent error
                return JsonResponse(
                    {"status": "error", "message": "Missing book_id in metadata"},
                    status=200,
                )

            if not customer_email:
                logger.error(
                    "Missing customer email in session (session: %s)", session_id
                )
                # Return 200 to acknowledge - missing email is a permanent error
                return JsonResponse(
                    {"status": "error", "message": "Missing customer email"},
                    status=200,
                )

            # Idempotency check: skip if order already processed
            if Order.objects.filter(stripe_session_id=session_id).exists():
                logger.info(
                    "Order already processed for session %s, returning 200", session_id
                )
                return JsonResponse(
                    {"status": "success", "message": "Order already processed"},
                    status=200,
                )

            shipping_details = session.get("shipping_details", {})
            address = shipping_details.get("address", {})
            amount_total = session.get("amount_total")

            if amount_total is None:
                logger.error(
                    "Missing amount_total in session (session: %s)", session_id
                )
                # Return 200 to acknowledge - won't gain amount_total on retry
                return JsonResponse(
                    {"status": "error", "message": "Missing amount_total"},
                    status=200,
                )

            # Initialize variables to avoid "possibly unbound" errors
            order = None
            needs_refund = False
            book_title = ""
            book_author = ""

            try:
                with transaction.atomic():
                    try:
                        book = Book.objects.select_for_update().get(id=book_id)
                    except Book.DoesNotExist:
                        logger.error(
                            "Book not found: %s (session: %s)", book_id, session_id
                        )
                        # Return 200 to acknowledge - book was deleted, no retry will help
                        return JsonResponse(
                            {
                                "status": "error",
                                "message": f"Book {book_id} not found",
                            },
                            status=200,
                        )

                    if not book.is_available:
                        # Capture book info before exiting atomic block
                        book_title = book.title
                        book_author = book.author
                        needs_refund = True
                    else:
                        needs_refund = False

                    if not needs_refund:
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
                            book_isbn=book.isbn or "",
                            book_price=book.price,
                            stripe_session_id=session_id,
                            customer_email=customer_email,
                            amount_paid=amount_paid,
                            shipping_name=shipping_details.get("name") or "",
                            shipping_address_line1=address.get("line1") or "",
                            shipping_address_line2=address.get("line2") or "",
                            shipping_city=address.get("city") or "",
                            shipping_state=address.get("state") or "",
                            shipping_postal_code=address.get("postal_code") or "",
                            shipping_country=address.get("country") or "",
                        )
                        logger.info("Created order %s for book %s", order.id, book_id)

            except IntegrityError as e:
                if "stripe_session_id" in str(e):
                    logger.info(
                        "Order for session %s already exists (duplicate webhook), returning 200",
                        session_id,
                    )
                else:
                    logger.error(
                        "IntegrityError creating order (session: %s): %s",
                        session_id,
                        e,
                    )
                return JsonResponse(
                    {"status": "success", "message": "Order already processed"},
                    status=200,
                )

            except Exception as e:
                logger.exception(
                    "Failed to process order (session: %s): %s", session_id, e
                )
                return JsonResponse(
                    {"status": "error", "message": "Order processing failed"},
                    status=500,
                )

            # Handle race condition refund outside the atomic block
            if needs_refund:
                logger.warning(
                    "Book %s is already sold (session: %s) - issuing refund",
                    book_id,
                    session_id,
                )

                # Refund the customer since book is already sold
                payment_intent = session.get("payment_intent")
                refund_status = "not attempted"
                if payment_intent:
                    try:
                        stripe.Refund.create(payment_intent=payment_intent)
                        refund_status = "succeeded"
                        logger.info(
                            "Refund created for payment_intent %s (session: %s)",
                            payment_intent,
                            session_id,
                        )
                    except stripe.error.StripeError as e:
                        refund_status = f"failed: {str(e)}"
                        logger.error(
                            "Failed to create refund for payment_intent %s (session: %s): %s",
                            payment_intent,
                            session_id,
                            str(e),
                        )

                # Notify admin about the race condition and refund (regardless of refund status)
                if settings.ADMINS:
                    try:
                        subject = (
                            "[bookstore] RACE CONDITION: Refund issued for sold book"
                        )
                        body = f"""A race condition occurred during checkout.

Book: {book_title} by {book_author} (ID: {book_id})
Customer Email: {customer_email}
Stripe Session: {session_id}
Payment Intent: {payment_intent}
Amount: £{Decimal(amount_total) / Decimal(100):.2f}

Refund Status: {refund_status}

The customer attempted to purchase a book that was already sold to another customer.
A refund has been {"processed" if refund_status == "succeeded" else "attempted"}.
"""
                        send_mail(
                            subject,
                            body,
                            settings.DEFAULT_FROM_EMAIL,
                            settings.ADMINS,
                            fail_silently=False,
                        )
                        logger.info("Admin notification sent for race condition refund")
                    except Exception as e:
                        logger.error("Failed to send admin notification: %s", str(e))

                # Notify customer about the refund
                try:
                    send_race_condition_refund_notification(
                        book_title,
                        book_author,
                        customer_email,
                        amount_total,
                        refund_status,
                    )
                    logger.info(
                        "Customer refund notification sent for session %s",
                        session_id,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to send customer refund notification: %s",
                        str(e),
                    )

                return JsonResponse(
                    {
                        "status": "success",
                        "message": "Book already sold - refund issued",
                    },
                    status=200,
                )

            # Send emails outside the transaction to avoid 500 on SMTP failure
            if order:
                try:
                    send_purchase_confirmation(order)
                    logger.info("Sent purchase confirmation for order %s", order.id)
                except Exception:
                    logger.exception(
                        "Failed to send confirmation email for order %s", order.id
                    )

                try:
                    send_admin_notification(order)
                    logger.info("Sent admin notification for order %s", order.id)
                except Exception:
                    logger.exception(
                        "Failed to send admin notification for order %s", order.id
                    )

                return JsonResponse(
                    {"status": "success", "message": "Order processed successfully"},
                    status=200,
                )

        except Exception as e:
            logger.exception(
                "Unexpected error processing checkout.session.completed (session: %s): %s",
                session_id,
                e,
            )
            return JsonResponse(
                {"status": "error", "message": "Processing error"},
                status=500,
            )

    return JsonResponse({"status": "success", "message": "Event received"})


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


def send_race_condition_refund_notification(
    book_title, book_author, customer_email, amount_total, refund_status
):
    """Send notification to customer when refunded due to race condition."""
    subject = f"[bookstore] Order Canceled - {book_title}"
    amount = Decimal(amount_total) / Decimal(100)

    if refund_status == "succeeded":
        refund_message = f"""You have been issued a full refund of £{amount:.2f}. The refund will appear on your payment method within 5 to 10 business days, depending on your bank or card issuer."""
    elif refund_status == "not attempted":
        refund_message = f"""We were unable to process a refund automatically. Our team has been notified and will manually issue a full refund of £{amount:.2f} to your payment method within 24 hours."""
    else:
        refund_message = f"""We encountered an issue processing your refund automatically. Our team has been notified and will manually issue a full refund of £{amount:.2f} to your payment method within 24 hours."""

    body = f"""We're sorry, but we were unable to complete your purchase.

BOOK DETAILS
-----
Title: {book_title}
Author: {book_author}
Price: £{amount:.2f}

WHAT HAPPENED
-----
Unfortunately, this book was sold to another customer just moments before your order was completed. We know this is disappointing, and we sincerely apologize for the inconvenience.

REFUND INFORMATION
-----
{refund_message}

If you have any questions or need assistance, please contact us.

Thank you for your understanding,
The Bookstore Team
"""

    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [customer_email],
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
