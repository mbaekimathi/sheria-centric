#!/usr/bin/env python3
"""
Seed default firm website / settings content into the database.

Normally runs once automatically on app init. This script is for manual use.
Pass --force to re-run after the one-shot marker is set.

Usage (from project root):
  python scripts/seed_firm_defaults.py
  python scripts/seed_firm_defaults.py --force
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Ensure local env is loaded the same way as the app
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, '.env'))
except Exception:
    pass


def main():
    import app as firm_app

    force = '--force' in sys.argv
    conn = firm_app.get_db_connection()
    if not conn:
        print('[ERROR] Could not connect to the database.')
        return 1
    try:
        # Ensure core firm tables exist first
        firm_app.create_company_settings_table()
        firm_app.create_firm_practice_areas_table()
        firm_app.create_firm_testimonials_table()
        firm_app.create_firm_notable_case_outcomes_table()
        firm_app.create_firm_media_mentions_table()
        firm_app.create_firm_awards_table()
        firm_app.create_firm_page_seo_table()
        firm_app.create_firm_image_alt_table()
        firm_app.create_firm_faqs_table()
        firm_app.create_firm_blog_posts_table()
        firm_app.create_attorney_education_table()

        summary = firm_app._seed_default_firm_website_content(conn, force=force)
        if summary.get('skipped'):
            print('Seed already completed once — skipped. Use --force to re-run.')
        else:
            print('Seed summary:', summary)
        return 0 if summary.get('ok') else 1
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
