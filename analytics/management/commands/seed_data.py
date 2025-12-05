import random
from datetime import timedelta
from django.utils import timezone
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from analytics.models import Blog, BlogView, Country
from faker import Faker


class Command(BaseCommand):
    help = "Seeds the database"

    def handle(self, *args, **kwargs):
        fake = Faker()
        self.stdout.write("🌱 Starting seed...")

        self.stdout.write("Creating Countries...")
        country_codes = ["US", "ET", "DE", "IN", "GB", "FR", "CA", "BR"]
        countries_objs = []
        for code in country_codes:
            c, _ = Country.objects.get_or_create(
                code=code, defaults={"name": f"Country {code}"}
            )
            countries_objs.append(c)

        self.stdout.write("Creating Users...")
        users = [
            User(username=fake.unique.user_name(), email=fake.email())
            for _ in range(20)
        ]
        User.objects.bulk_create(users, ignore_conflicts=True)
        users = list(User.objects.all())

        self.stdout.write("Creating Blogs...")
        blogs = [
            Blog(title=fake.catch_phrase(), author=random.choice(users), content="...")
            for _ in range(50)
        ]
        Blog.objects.bulk_create(blogs)
        blogs = list(Blog.objects.all())

        self.stdout.write("Creating 10,000 Views...")
        views = []
        end_time = timezone.now()
        for _ in range(10000):
            views.append(
                BlogView(
                    blog=random.choice(blogs),
                    country=random.choice(countries_objs),
                    timestamp=end_time - timedelta(days=random.randint(0, 365)),
                    ip_address=fake.ipv4(),
                )
            )
        BlogView.objects.bulk_create(views, batch_size=2000)
        self.stdout.write(self.style.SUCCESS("Done!"))
