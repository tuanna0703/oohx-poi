"""Chain catalogs: a chain's own store list and what each store sells.

Separate from the POI pipeline on purpose. A chain's store list is matched to
existing masters by distance (``services.catalog.match_stores``) instead of
going through normalize + dedupe, and products have nothing to do with places.
"""
