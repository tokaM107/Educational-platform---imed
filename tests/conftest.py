"""Test process configuration required before application modules are imported."""

import os


os.environ.setdefault(
    "NEST_JWT_ACCESS_SECRET",
    "nest-test-access-secret-at-least-32-characters-and-long-enough",
)
os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")
os.environ.setdefault("SUPABASE_PUBLISHABLE_KEY", "test-publishable-key")
