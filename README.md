# DeFlock KML Export

Modified version of [deflock-kml-export](https://github.com/daTechGuy/deflock-kml-export) for creating exports capable of being imported as avoidance packs into [ALPRwatch.org Maps](https://alprwatch.org/navigation/)
by drawing vision cones/circles around cameras instead of just points where the cameras are located.

## Configuration

Everything is configured via environment variables:

* OVERPASS_MIRRORS: URLs called for overpass queries
* REQUEST_TIMEOUT_SECONDS: Timeout duration for overpass queries
* RETRIES_PER_MIRROR: Amount of times to retry failed overpass queries
* RETRY_BACKOFF_SECONDS: Time to wait between failed overpass queries
* CAMERA_VISION_RANGE: Length of vision cone/circle to draw from camera location (meters)
* CAMERA_VISION_FOV: Camera field of view when drawing vision cones
* INCLUDED_STATES: States to include in export
* OUTPUT_PATH: Path to write kml file(s)
* SCHEDULE_CRONTIME: Crontime for when to automatically run

## Scheduling

If you do not set the SCHEDULE_CRONTIME variable the script will run once & exit.

If it is set the script uses [pycron](https://github.com/kipe/pycron) to run the script automatically on the given interval, should accept most [cron](https://en.wikipedia.org/wiki/Cron) formats

## Data source and license

Camera data comes from [OpenStreetMap](https://www.openstreetmap.org/), and
is licensed under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).
If you republish or redistribute the data (not just use it privately), you're
bound by ODbL's share-alike and attribution terms.
