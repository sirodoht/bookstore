"""Django views for books app."""

import json
import logging
from decimal import Decimal

import openai
import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.files.base import ContentFile
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import CreateView, ListView, TemplateView, UpdateView

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


class BookUpdateView(UserPassesTestMixin, UpdateView):
    """Update an existing book (admin only)."""

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

    def get(self, request, pk):
        """Redirect GET requests to book list."""
        messages.info(request, "Please use the purchase button to buy this book.")
        return redirect("books:book-list")

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
                            "unit_amount": int((book.price * 100).to_integral_value()),
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
                        Book.objects.filter(id=book.id).update(is_available=False)
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
                    except stripe.StripeError as e:
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


class BookBatchUploadView(UserPassesTestMixin, TemplateView):
    """Display batch upload form for multiple book covers (admin only)."""

    template_name = "books/batch_upload.html"
    login_url = "/admin/login/"

    def test_func(self):
        """Only allow admin users."""
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        """Add max files limit to context."""
        context = super().get_context_data(**kwargs)
        context["max_files"] = 10
        return context


@user_passes_test(lambda u: u.is_staff)
def batch_upload_stream(request):
    """Stream batch upload progress via Server-Sent Events."""
    if request.method != "POST":
        return JsonResponse({"error": "Only POST requests allowed"}, status=405)

    files = request.FILES.getlist("cover_images")

    if not files:
        return JsonResponse({"error": "No files provided"}, status=400)

    if len(files) > 10:
        return JsonResponse({"error": "Maximum 10 files allowed"}, status=400)

    def event_stream():
        """Generate SSE events for each file processed."""
        from . import openai as openai_module

        results = []
        total = len(files)

        for idx, file in enumerate(files, 1):
            result = {
                "index": idx,
                "total": total,
                "filename": file.name,
                "status": "processing",
                "book_id": None,
                "book_title": None,
                "error": None,
            }

            # Send processing start event
            yield f"data: {json.dumps(result)}\n\n"

            try:
                # Read and analyze the image
                image_data = file.read()
                analysis = openai_module.analyze_cover_image(image_data)

                if not analysis["success"]:
                    result["status"] = "failed"
                    result["error"] = analysis.get("error", "Analysis failed")
                    results.append(result)
                    yield f"data: {json.dumps(result)}\n\n"
                    continue

                # Parse published year
                published_year = None
                if analysis.get("published_year"):
                    try:
                        year_str = analysis["published_year"].strip()
                        if year_str.isdigit():
                            published_year = int(year_str)
                    except (ValueError, AttributeError):
                        pass

                # Create the book with is_available=False
                book = Book.objects.create(
                    title=analysis.get("title", "") or "Untitled",
                    author=analysis.get("author", "") or "Unknown Author",
                    description=analysis.get("description", ""),
                    published_year=published_year,
                    price=Decimal("10.00"),
                    is_available=False,
                )

                # Save the cover image
                if image_data:
                    book.cover_image.save(
                        file.name,
                        ContentFile(image_data),
                        save=True,
                    )

                result["status"] = "completed"
                result["book_id"] = book.id
                result["book_title"] = book.title

                logger.info(
                    "batch_upload: Created book %s from %s",
                    book.id,
                    file.name,
                )

            except Exception as e:
                logger.exception("batch_upload: Failed to process %s: %s", file.name, e)
                result["status"] = "failed"
                result["error"] = str(e)

            results.append(result)
            yield f"data: {json.dumps(result)}\n\n"

        # Send completion event with all results
        completion = {
            "status": "complete",
            "total": total,
            "completed": sum(1 for r in results if r["status"] == "completed"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
            "results": results,
        }
        yield f"data: {json.dumps(completion)}\n\n"

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@user_passes_test(lambda u: u.is_staff)
def batch_results(request):
    """Display results of batch upload."""
    results_json = request.GET.get("results")

    if not results_json:
        messages.warning(request, "No batch results to display.")
        return redirect("books:book-list")

    try:
        results = json.loads(results_json)
    except json.JSONDecodeError:
        messages.error(request, "Invalid batch results data.")
        return redirect("books:book-list")

    completed_books = []
    failed_uploads = []

    for result in results:
        if result["status"] == "completed" and result.get("book_id"):
            try:
                book = Book.objects.get(id=result["book_id"])
                completed_books.append(
                    {
                        "book": book,
                        "filename": result["filename"],
                    }
                )
            except Book.DoesNotExist:
                failed_uploads.append(
                    {
                        "filename": result["filename"],
                        "error": "Book was deleted",
                    }
                )
        elif result["status"] == "failed":
            failed_uploads.append(
                {
                    "filename": result["filename"],
                    "error": result.get("error", "Unknown error"),
                }
            )

    context = {
        "completed_books": completed_books,
        "failed_uploads": failed_uploads,
        "total": len(results),
        "completed_count": len(completed_books),
        "failed_count": len(failed_uploads),
    }

    return render(request, "books/batch_results.html", context)


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
    except Exception as e:
        logger.error("analyze_cover: Failed to read image: %s", e, exc_info=True)
        return JsonResponse({"error": "Failed to process image"}, status=500)

    from . import openai as openai_module

    analysis = openai_module.analyze_cover_image(image_data)

    if not analysis["success"]:
        error_msg = analysis.get("error", "Analysis failed")
        status_code = 500
        if "rate limited" in error_msg.lower():
            status_code = 429
        elif "not configured" in error_msg.lower():
            status_code = 500
        return JsonResponse({"error": error_msg}, status=status_code)

    return JsonResponse(
        {
            "title": analysis.get("title", ""),
            "author": analysis.get("author", ""),
            "description": analysis.get("description", ""),
            "published_year": analysis.get("published_year", ""),
        }
    )
