from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection


class Command(BaseCommand):
    help = "Execute a safe PostgreSQL-readiness check and print non-secret diagnostics."

    def handle(self, *args, **options):
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1, current_database(), current_user, inet_server_port(), version()"
                )
                result, database_name, user_name, port, pg_version = cursor.fetchone()
        except DatabaseError as exc:  # pragma: no cover - depends on local database environment
            raise CommandError("Database unavailable; check the database settings and server.") from exc

        if result != 1:
            raise CommandError("Database query returned an unexpected result.")

        self.stdout.write(self.style.SUCCESS(f"Database: {database_name}"))
        self.stdout.write(self.style.SUCCESS(f"Role: {user_name}"))
        self.stdout.write(self.style.SUCCESS(f"Port: {port}"))
        self.stdout.write(self.style.SUCCESS(f"PostgreSQL version: {pg_version}"))
