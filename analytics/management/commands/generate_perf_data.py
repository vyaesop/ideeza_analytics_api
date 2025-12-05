from django.core.management.base import BaseCommand
from django.utils import timezone
from analytics.models import Blog, BlogView, Country
from django.contrib.auth.models import User
import random


class Command(BaseCommand):
    help = "Generate lightweight performance test data: blogs and views"

    def add_arguments(self, parser):
        parser.add_argument("--blogs", type=int, default=2, help="Number of blogs to create")
        parser.add_argument("--views", type=int, default=10, help="Number of views to create")

    def handle(self, *args, **options):
        num_blogs = options.get("blogs", 2)
        num_views = options.get("views", 10)

        user, _ = User.objects.get_or_create(username="perf_user")
        country, _ = Country.objects.get_or_create(name="USA", code="US")

        blogs = []
        for i in range(num_blogs):
            b = Blog.objects.create(title=f"Perf Blog {i}", author=user, content="perf")
            blogs.append(b)

        now = timezone.now()
        for i in range(num_views):
            blog = random.choice(blogs)
            BlogView.objects.create(blog=blog, country=country, timestamp=now)

        self.stdout.write(self.style.SUCCESS(f"Created {num_blogs} blogs and {num_views} views"))
