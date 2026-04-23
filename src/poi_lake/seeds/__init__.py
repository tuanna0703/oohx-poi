"""Seed data for reference tables.

- ``sources.py``          -> rows for ``sources``
- ``openooh_taxonomy.py`` -> rows for ``openooh_categories`` (v1.1)
- ``vn_brands.py``        -> rows for ``brands`` (Vietnam market)

The ``runner`` module idempotently upserts these into the DB.
"""
