# DeFlock KML Export

Modified version of [deflock-kml-export](https://github.com/daTechGuy/deflock-kml-export) that removes a lot of the manual options, 
it's designed to be run as a container which automatically updates every week

the older deflock_kml.py still contains the original manual options (with the ability to input multiple states & drawing vision cones/circles)

## Data source and license

Camera data comes from [OpenStreetMap](https://www.openstreetmap.org/), and
is licensed under the [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).
If you republish or redistribute the data (not just use it privately), you're
bound by ODbL's share-alike and attribution terms.

The public Overpass API mirrors this script queries are free, shared
infrastructure. Please don't run `--all-us` in a tight loop -- the built-in
throttling is there deliberately, not just for show.
