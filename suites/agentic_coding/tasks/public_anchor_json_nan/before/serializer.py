import json


def serialize_metrics(metrics):
    """Serialize a metrics dict to JSON for the ingestion pipeline.

    `metrics` may contain float('nan') or float('inf') from sensor readings.
    """
    return json.dumps(metrics)
