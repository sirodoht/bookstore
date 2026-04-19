"""Tests for the books app."""

import io
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Book


class TempMediaRootMixin:
    """Use an isolated MEDIA_ROOT for file-based tests."""

    def setUp(self):
        super().setUp()
        self.temp_media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.temp_media_root)
        self.media_override.enable()

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.temp_media_root, ignore_errors=True)
        super().tearDown()

    def make_cover_upload(self, name="cover.png", size=(1200, 1600), color="navy"):
        """Create an in-memory image upload."""
        image = Image.new("RGB", size, color=color)
        output = io.BytesIO()
        image.save(output, format="PNG")
        content = output.getvalue()
        return SimpleUploadedFile(name, content, content_type="image/png")

    def stored_cover_files(self):
        """Return stored book cover files under MEDIA_ROOT."""
        cover_dir = Path(settings.MEDIA_ROOT) / "book_covers"
        if not cover_dir.exists():
            return []
        return sorted(path for path in cover_dir.iterdir() if path.is_file())


class BookCoverImageTests(TempMediaRootMixin, TestCase):
    def test_new_cover_upload_is_stored_once_as_processed_image(self):
        book = Book(
            title="Single Upload",
            author="Author",
            cover_image=self.make_cover_upload(),
        )

        book.save()

        stored_files = self.stored_cover_files()

        self.assertEqual(len(stored_files), 1)
        self.assertTrue(book.cover_image.name.endswith(".jpg"))

        with Image.open(stored_files[0]) as processed_image:
            self.assertEqual(processed_image.size, (780, 1080))

    def test_saving_without_changing_cover_does_not_write_a_second_file(self):
        book = Book(
            title="Original Title",
            author="Author",
            cover_image=self.make_cover_upload(),
        )
        book.save()

        original_cover_name = book.cover_image.name

        book.title = "Updated Title"
        book.save()

        stored_files = self.stored_cover_files()

        self.assertEqual(len(stored_files), 1)
        self.assertEqual(book.cover_image.name, original_cover_name)


class BatchUploadTests(TempMediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff_user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)

    @patch("books.openai.analyze_cover_image")
    def test_batch_upload_stores_each_cover_once(self, mock_analyze_cover_image):
        mock_analyze_cover_image.return_value = {
            "title": "Batch Upload",
            "author": "Author",
            "description": "Description",
            "published_year": "2024",
            "success": True,
        }

        response = self.client.post(
            reverse("books:batch-upload-stream"),
            {"cover_images": [self.make_cover_upload(name="batch.png")]},
        )

        self.assertEqual(response.status_code, 200)
        list(response.streaming_content)

        book = Book.objects.get()
        stored_files = self.stored_cover_files()

        self.assertEqual(len(stored_files), 1)
        self.assertTrue(book.cover_image.name.endswith(".jpg"))
