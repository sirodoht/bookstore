"""Stripe webhook handlers for the books app."""

import logging
from decimal import Decimal

import stripe
from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .emails import (
    send_admin_notification,
    send_purchase_confirmation,
    send_race_condition_refund_notification,
)
from .models import Book, Order

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


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

            shipping_details = session.get("collected_information", {}).get(
                "shipping_details", {}
            ) or session.get("shipping_details", {})
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
