"""Cleanup orphaned book cover images not referenced by any book."""

import os

from django.conf import settings
from django.core.management.base import BaseCommand

from books.models import Book


class Command(BaseCommand):
    help = "Delete orphaned book cover images not referenced by any book"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview orphaned files without deleting them",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Path to book covers directory
        covers_dir = settings.MEDIA_ROOT / "book_covers"

        # Get all referenced cover image filenames
        referenced_covers = set()
        for book in Book.objects.exclude(cover_image=""):
            if book.cover_image:
                # cover_image.name gives the relative path like "book_covers/uuid.jpeg"
                referenced_covers.add(os.path.basename(book.cover_image.name))

        # Scan directory and find orphaned files
        total_files = 0
        orphaned_files = []

        if covers_dir.exists():
            for file_path in covers_dir.iterdir():
                if file_path.is_file():
                    total_files += 1
                    filename = file_path.name
                    if filename not in referenced_covers:
                        orphaned_files.append(file_path)
        else:
            self.stdout.write(
                self.style.WARNING(f"Covers directory does not exist: {covers_dir}")
            )
            return

        # Report findings
        self.stdout.write(f"Total files in directory: {total_files}")
        self.stdout.write(f"Books with covers: {len(referenced_covers)}")
        self.stdout.write(f"Orphaned files: {len(orphaned_files)}")

        if not orphaned_files:
            self.stdout.write(self.style.SUCCESS("No orphaned files to clean up"))
            return

        # Calculate size of orphaned files
        orphaned_bytes = sum(f.stat().st_size for f in orphaned_files)
        self.stdout.write(f"Space to be freed: {orphaned_bytes / 1024 / 1024:.2f} MB")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No files will be deleted"))
            self.stdout.write("Orphaned files:")
            for file_path in orphaned_files:
                size_mb = file_path.stat().st_size / 1024 / 1024
                self.stdout.write(f"  - {file_path.name} ({size_mb:.2f} MB)")
            return

        # Delete orphaned files
        deleted_count = 0
        freed_bytes = 0

        for file_path in orphaned_files:
            try:
                file_size = file_path.stat().st_size
                file_path.unlink()
                deleted_count += 1
                freed_bytes += file_size
            except OSError as e:
                self.stdout.write(
                    self.style.ERROR(f"Failed to delete {file_path.name}: {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_count}/{len(orphaned_files)} files, "
                f"freed {freed_bytes / 1024 / 1024:.2f} MB"
            )
        )
