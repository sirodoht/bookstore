"""Email utilities for the books app."""

import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


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
