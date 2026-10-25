from poi_lake.workers import consumed_queues


def test_named_queues():
    argv = ["dramatiq", "poi_lake.workers", "--processes", "2", "--queues", "ingest", "dedupe"]
    assert consumed_queues(argv) == {"ingest", "dedupe"}


def test_catalog_worker_does_not_consume_dedupe():
    argv = ["dramatiq", "poi_lake.workers", "--queues", "catalog", "--threads", "1"]
    assert consumed_queues(argv) == {"catalog"}


def test_no_queues_means_all():
    assert consumed_queues(["dramatiq", "poi_lake.workers", "-p", "2"]) is None
