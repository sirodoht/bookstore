"""Django views for books app."""

import json
import logging
import random
import time
from decimal import Decimal

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.files.base import ContentFile
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
)

from . import adj, amazon_scraper
from .models import Book, Tag


def logout_view(request):
    """Log out user and redirect to book list."""
    logout(request)
    return redirect("books:book-list")


logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


class BookListView(ListView):
    """List all available books."""

    model = Book
    template_name = "books/book_list.html"
    context_object_name = "books"

    def get_queryset(self):
        queryset = Book.objects.filter(is_available=True)
        sort = self.request.GET.get("sort")
        tag_slug = self.request.GET.get("tag")

        # Filter by tag if specified
        if tag_slug:
            queryset = queryset.filter(tags__slug=tag_slug)

        if sort == "title_asc":
            queryset = queryset.order_by("title")
        elif sort == "title_desc":
            queryset = queryset.order_by("-title")
        elif sort == "author_asc":
            queryset = queryset.order_by("author")
        elif sort == "author_desc":
            queryset = queryset.order_by("-author")
        else:
            queryset = queryset.order_by("?")

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sort"] = self.request.GET.get("sort")
        context["view"] = self.request.GET.get("view", "list")
        context["adjective"] = random.choice(adj.ADJECTIVE_LIST)
        context["banner_books"] = Book.objects.filter(is_available=True).order_by("?")
        context["all_tags"] = Tag.objects.all()
        context["active_tag"] = self.request.GET.get("tag")
        return context


class BookDetailView(DetailView):
    """Display details of a single book."""

    model = Book
    template_name = "books/book_detail.html"
    context_object_name = "book"

    def get_queryset(self):
        return Book.objects.filter(is_available=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["adjective"] = random.choice(adj.ADJECTIVE_LIST)
        context["banner_books"] = Book.objects.filter(is_available=True).order_by("?")
        return context


class BookCreateView(UserPassesTestMixin, CreateView):
    """Create a new book (admin only)."""

    model = Book
    template_name = "books/staff_book_form.html"
    fields = [
        "title",
        "author",
        "isbn",
        "description",
        "review",
        "published_year",
        "price",
        "is_available",
        "cover_image",
        "tags",
    ]
    success_url = reverse_lazy("books:book-list")
    login_url = "/admin/login/"

    def test_func(self):
        """Only allow admin users."""
        return self.request.user.is_staff


class BookUpdateView(UserPassesTestMixin, View):
    """Redirect to Django admin edit page (admin only)."""

    login_url = "/admin/login/"

    def test_func(self):
        """Only allow admin users."""
        return self.request.user.is_staff

    def get(self, request, pk):
        """Redirect to Django admin change page."""
        return redirect(f"/admin/books/book/{pk}/change/")


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


class AboutView(TemplateView):
    """Display about page."""

    template_name = "books/about.html"


class BookBatchUploadView(UserPassesTestMixin, TemplateView):
    """Display batch upload form for multiple book covers (admin only)."""

    template_name = "books/staff_batch_upload.html"
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

                # Save the book and uploaded cover in one model save so the image
                # is processed once before it is written to storage.
                book = Book(
                    title=analysis.get("title", "") or "Untitled",
                    author=analysis.get("author", "") or "Unknown Author",
                    description=analysis.get("description", ""),
                    published_year=published_year,
                    price=Decimal("4.00"),
                    is_available=False,
                )

                if image_data:
                    book.cover_image = ContentFile(image_data, name=file.name)

                book.save()

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

    return render(request, "books/staff_batch_results.html", context)


class AmazonAddView(UserPassesTestMixin, TemplateView):
    """Display form for adding books from Amazon links (admin only)."""

    template_name = "books/staff_amazon_add.html"
    login_url = "/admin/login/"

    def test_func(self):
        """Only allow admin users."""
        return self.request.user.is_staff

    def get_context_data(self, **kwargs):
        """Add max links limit to context."""
        context = super().get_context_data(**kwargs)
        context["max_links"] = 10
        return context


@user_passes_test(lambda u: u.is_staff)
def amazon_add_stream(request):
    """Stream Amazon book import progress via Server-Sent Events."""
    if request.method != "POST":
        return JsonResponse({"error": "Only POST requests allowed"}, status=405)

    links_text = request.POST.get("amazon_links", "").strip()

    if not links_text:
        return JsonResponse({"error": "No links provided"}, status=400)

    # Parse links (one per line)
    links = [link.strip() for link in links_text.split("\n") if link.strip()]

    if not links:
        return JsonResponse({"error": "No valid links provided"}, status=400)

    if len(links) > 10:
        return JsonResponse({"error": "Maximum 10 links allowed"}, status=400)

    def event_stream():
        """Generate SSE events for each link processed."""
        results = []
        total = len(links)

        for idx, url in enumerate(links, 1):
            result = {
                "index": idx,
                "total": total,
                "url": url,
                "status": "processing",
                "book_id": None,
                "book_title": None,
                "error": None,
            }

            # Send processing start event
            yield f"data: {json.dumps(result)}\n\n"

            try:
                # Follow redirect for amzn.to links
                real_url = amazon_scraper.follow_redirect(url)

                if isinstance(real_url, dict) and "error" in real_url:
                    raise Exception(real_url["error"])

                # Add 1-second delay before scraping
                time.sleep(1)

                # Scrape book data
                data = amazon_scraper.scrape_book_data(real_url)

                if "error" in data:
                    raise Exception(data["error"])

                # Download cover image
                image_data = None
                if data.get("cover_url"):
                    image_data = amazon_scraper.download_image(data["cover_url"])

                # Convert ISBN-10 to ISBN-13
                isbn13 = None
                if data.get("isbn"):
                    isbn13 = amazon_scraper.isbn10_to_isbn13(data["isbn"])

                # Create the book
                book = Book(
                    title=data.get("title", "Untitled"),
                    author=data.get("author", "Unknown Author"),
                    isbn=isbn13,
                    published_year=data.get("year"),
                    price=Decimal("4.00"),
                    is_available=True,
                    amazon_link=url,
                )

                if image_data:
                    book.cover_image = ContentFile(image_data, name="cover.jpg")

                book.save()

                result["status"] = "completed"
                result["book_id"] = book.id
                result["book_title"] = book.title

                logger.info(
                    "amazon_add: Created book %s from %s",
                    book.id,
                    url,
                )

            except Exception as e:
                logger.exception("amazon_add: Failed to process %s: %s", url, e)
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
def amazon_results(request):
    """Display results of Amazon book import."""
    results_json = request.GET.get("results")

    if not results_json:
        messages.warning(request, "No Amazon import results to display.")
        return redirect("books:book-list")

    try:
        results = json.loads(results_json)
    except json.JSONDecodeError:
        messages.error(request, "Invalid Amazon import results data.")
        return redirect("books:book-list")

    completed_books = []
    failed_imports = []

    for result in results:
        if result["status"] == "completed" and result.get("book_id"):
            try:
                book = Book.objects.get(id=result["book_id"])
                completed_books.append(
                    {
                        "book": book,
                        "url": result["url"],
                    }
                )
            except Book.DoesNotExist:
                failed_imports.append(
                    {
                        "url": result["url"],
                        "error": "Book was deleted",
                    }
                )
        elif result["status"] == "failed":
            failed_imports.append(
                {
                    "url": result["url"],
                    "error": result.get("error", "Unknown error"),
                }
            )

    context = {
        "completed_books": completed_books,
        "failed_imports": failed_imports,
        "total": len(results),
        "completed_count": len(completed_books),
        "failed_count": len(failed_imports),
    }

    return render(request, "books/staff_amazon_results.html", context)
