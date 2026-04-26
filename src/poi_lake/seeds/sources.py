"""Source registry seed data.

All sources are seeded with ``enabled=False`` so nothing starts ingesting until
an operator reviews credentials and flips the switch.
"""

from __future__ import annotations

from typing import Any

SOURCES: list[dict[str, Any]] = [
    {
        "code": "google_places",
        "name": "Google Places API (New)",
        "adapter_class": "poi_lake.adapters.google_places:GooglePlacesAdapter",
        "config": {
            "rate_limit_per_second": 50,
            "timeout_seconds": 30,
            "field_mask": "places.id,places.displayName,places.formattedAddress,places.location,places.types,places.primaryType,places.nationalPhoneNumber,places.internationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount",
            "cache_ttl_days": 30,
        },
        "enabled": False,
        "priority": 10,
    },
    {
        "code": "osm_overpass",
        "name": "OpenStreetMap (Overpass API)",
        "adapter_class": "poi_lake.adapters.osm_overpass:OSMOverpassAdapter",
        "config": {
            "rate_limit_per_second": 1,
            "timeout_seconds": 180,
            "self_hosted": False,
        },
        "enabled": False,
        "priority": 20,
    },
    {
        "code": "gosom_scraper",
        "name": "gosom google-maps-scraper (enrichment sidecar)",
        "adapter_class": "poi_lake.adapters.gosom_scraper:GosomScraperAdapter",
        "config": {
            # Submission rate (jobs/sec). Each gosom job paces its own scrape
            # internally; this caps how many we kick off per second.
            "rate_limit_per_second": 0.2,
            "timeout_seconds": 30,         # HTTP timeout per API call
            "lang": "vi",
            "depth": 2,                    # 2 scroll batches ~= 30-40 results
            "zoom": 15,                    # overridden by adapter from radius
            "fast_mode": True,             # skip reviews+popular_times for speed
            "fetch_emails": False,
            "poll_interval_s": 5,
            "max_wait_s": 600,             # 10 minutes hard cap per job
            "cleanup_jobs": True,
        },
        "enabled": False,
        "priority": 30,
    },
    {
        "code": "vietmap",
        "name": "Vietmap Maps API",
        "adapter_class": "poi_lake.adapters.vietmap:VietmapAdapter",
        "config": {
            "rate_limit_per_second": 10,
            "timeout_seconds": 30,
            "base_url": "https://maps.vietmap.vn/api",
        },
        "enabled": False,
        "priority": 15,
    },
    {
        "code": "foody",
        "name": "Foody.vn (scraping)",
        "adapter_class": "poi_lake.adapters.foody:FoodyAdapter",
        "config": {
            "rate_limit_per_second": 2,
            "timeout_seconds": 30,
            "respect_robots_txt": True,
            "rotate_user_agents": True,
        },
        "enabled": False,
        "priority": 40,
    },
]
