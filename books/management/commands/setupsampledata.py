from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Load sample books and create admin user"

    def handle(self, *args, **options):
        # Load books fixture
        self.stdout.write("Loading sample books...")
        call_command("loaddata", "books")
        self.stdout.write(self.style.SUCCESS("Sample books loaded!"))

        # Create admin user
        self.stdout.write("Creating admin user...")
        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "", "admin")
            self.stdout.write(self.style.SUCCESS("Admin user created!"))
            self.stdout.write("Username: admin")
            self.stdout.write("Password: admin")
        else:
            self.stdout.write(self.style.WARNING("Admin user already exists"))
