#!/usr/bin/env python3
"""
deflock_kml.py -- pull ALPR (license plate reader) camera locations from
OpenStreetMap (the same data DeFlock.me itself displays) and write them out
as KML files you can import into Google My Maps / Google Earth / QGIS.

DeFlock has no database of its own: every camera on deflock.me comes from
OpenStreetMap nodes tagged surveillance:type=ALPR, queried live via the
Overpass API. This script does the same live query directly, so the data
is exactly as current as DeFlock's own map, not a stale snapshot.

Usage:
    python deflock_kml.py --state California
    python deflock_kml.py --state CA
    python deflock_kml.py --all-us
    python deflock_kml.py --list-states

No third-party dependencies -- standard library only, so anyone with a
plain Python 3 install can run this with no `pip install` step.
"""

import argparse
import http.client
import json
import os
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from xml.sax.saxutils import escape as xml_escape

# ============================================================================
# Overpass API access
# ============================================================================
#
# Public Overpass instances are shared, free, and rate-limited -- during
# development, the primary instance returned HTTP 429 (rate limited) and
# both instances occasionally returned connection failures under load. This
# retries across multiple mirrors with backoff rather than failing on the
# first hiccup, which is necessary in practice, not just defensive
# programming -- confirmed by hitting exactly these failures live.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
REQUEST_TIMEOUT_SECONDS = 180
RETRIES_PER_MIRROR = 65535
RETRY_BACKOFF_SECONDS = 30


REQUEST_HEADERS = {
    # Overpass API's own usage policy asks clients to identify themselves --
    # Python's default urllib User-Agent gets treated as generic bot traffic
    # by some servers/proxies and deprioritized or blocked outright. Setting
    # a real one closed a reliability gap observed directly: curl (which
    # sends its own descriptive UA by default) succeeded repeatedly against
    # these same mirrors during development, while unmodified urllib.request
    # failed immediately on the very same query.
    "User-Agent": "deflock-kml-export/1.0 (contact: run by end user; ALPR-mapping tool using the OpenStreetMap Overpass API)",
}


def run_overpass_query(query: str, label: str) -> dict:
    """POST a query to Overpass, retrying across mirrors. Returns parsed JSON."""
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error = ""
    for mirror in OVERPASS_MIRRORS:
        for attempt in range(1, RETRIES_PER_MIRROR + 1):
            try:
                req = urllib.request.Request(mirror, data=data, method="POST", headers=REQUEST_HEADERS)
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    body = resp.read()
                    parsed = json.loads(body)
                    if "elements" in parsed:
                        return parsed
                    last_error = "response had no 'elements' field"
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}"
                if e.code == 429:
                    # Rate limited -- give the server a real break, not just
                    # a token retry, before hitting it again.
                    time.sleep(RETRY_BACKOFF_SECONDS * 2)
                    continue
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                    http.client.HTTPException, ConnectionError, OSError) as e:
                # http.client.HTTPException (e.g. RemoteDisconnected, when a
                # server accepts the TCP connection then drops it before
                # sending a response) is a *different* exception hierarchy
                # than urllib.error.URLError -- confirmed live: this exact
                # case slipped past an earlier, narrower except clause and
                # produced a raw traceback instead of a clean retry.
                # ConnectionError/OSError are a deliberately broad net for
                # whatever other network-layer flakiness these free public
                # mirrors turn out to have.
                last_error = str(e)
            print(f"  [{label}] attempt {attempt} via {mirror} failed: {last_error}", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise RuntimeError(f"[{label}] all Overpass mirrors failed. Last error: {last_error}")


# ============================================================================
# US states -- name/abbreviation -> OSM ISO3166-2 code
# ============================================================================
# OSM tags US state boundary relations with ISO3166-2:US, e.g. "US-CA" for
# California. Querying by this code (via Overpass's `area` filter) gets
# every node inside the state's real administrative boundary -- no crude
# bounding box that clips corners or double-counts neighboring states at
# the edges.
US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "dc": "DC",
}
# All valid two-letter codes, for direct --state CA style input.
US_STATE_CODES = set(US_STATES.values())


def resolve_state_code(user_input: str) -> str:
    """Accepts a full state name (any case) or a 2-letter code. Returns the code."""
    s = user_input.strip()
    if s.upper() in US_STATE_CODES:
        return s.upper()
    key = s.lower()
    if key in US_STATES:
        return US_STATES[key]
    raise ValueError(
        f"'{user_input}' isn't a recognized US state name or code. "
        f"Run with --list-states to see valid options."
    )


CODE_TO_CANONICAL_NAME = {
    "DC": "District of Columbia",
}


def list_states() -> str:
    lines = ["Valid --state values (name or 2-letter code):"]
    seen_codes = set()
    for name, code in sorted(US_STATES.items()):
        if code in seen_codes:
            continue
        seen_codes.add(code)
        display_name = CODE_TO_CANONICAL_NAME.get(code, name.title())
        lines.append(f"  {code}  {display_name}")
    return "\n".join(lines)


# ============================================================================
# Fetching ALPR nodes
# ============================================================================

def fetch_state_nodes(state_code: str) -> list:
    """Fetch every surveillance:type=ALPR node inside one US state's boundary."""
    query = (
        "[out:json][timeout:150][maxsize:1073741824];"
        f'area["ISO3166-2"="US-{state_code}"]["admin_level"="4"]->.searchArea;'
        'node["surveillance:type"="ALPR"](area.searchArea);'
        "out body;"
    )
    result = run_overpass_query(query, state_code)
    return result.get("elements", [])


def fetch_all_us_nodes(progress_callback=None) -> list:
    """
    Fetches every state individually and merges, rather than one national
    query. Deliberate: a single query covering the whole US returned
    80,000+ nodes for just the eastern half during testing, and public
    Overpass instances were observed rate-limiting (HTTP 429) and dropping
    connections under load even on half-country-sized requests. Per-state
    queries are smaller, so a single failure only costs one state's retry,
    not the whole run.
    """
    all_nodes = []
    seen_ids = set()
    codes = sorted(set(US_STATES.values()))
    for i, code in enumerate(codes, 1):
        if progress_callback:
            progress_callback(i, len(codes), code)
        nodes = fetch_state_nodes(code)
        for n in nodes:
            # Dedup by OSM node id -- a camera near a state-line boundary
            # relation edge case, or a retry that partially succeeded
            # before failing, could otherwise appear twice.
            if n.get("id") not in seen_ids:
                seen_ids.add(n["id"])
                all_nodes.append(n)
        time.sleep(1)  # be a reasonable citizen of a free shared public API
    return all_nodes


# ============================================================================
# KML generation
# ============================================================================

GOOGLE_MY_MAPS_MAX_PER_LAYER = 2000  # hard limit; exceeding this is SILENTLY
                                      # truncated by Google My Maps with no
                                      # error shown -- see README.

def move_point(lat, lng, distance, bearing):
    """
    Returns (lat, lng) a `distance` meters away at `bearing` degrees.
    0 = north, 90 = east, 270 = west.
    """
    R = 6378137  # WGS84 mean radius in meters
    
    # Convert inputs to radians
    lat_rad = math.radians(lat)
    lng_rad = math.radians(lng)
    bearing_rad = math.radians(bearing)
    
    # Angular distance in radians
    d = distance / R
    
    # Calculate new latitude
    new_lat_rad = math.asin(
        math.sin(lat_rad) * math.cos(d) +
        math.cos(lat_rad) * math.sin(d) * math.cos(bearing_rad)
    )
    
    # Calculate new longitude
    new_lng_rad = lng_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(d) * math.cos(lat_rad),
        math.cos(d) - math.sin(lat_rad) * math.sin(new_lat_rad)
    )
    
    # Convert back to degrees
    new_lat = math.degrees(new_lat_rad)
    new_lng = math.degrees(new_lng_rad)
    
    return new_lat, new_lng

def circle_points(center_lat, center_lng, radius, num_points):
    """
    Returns a list of (lat, lng) points forming a circle around the center.
    """
    points = []
    for i in range(num_points):
        angle = (360 / num_points) * i  # degrees for each point
        # Convert angle to bearing (0 = north, clockwise)
        bearing = angle
        # Move `radius` meters at this bearing
        lat, lng = move_point(center_lat, center_lng, radius, bearing)
        points.append(f"{lng},{lat}")
    # Close circle
    points.append(points[0])
    return points

def node_to_placemark(node: dict) -> str:
    tags = node.get("tags", {})
    lat = node.get("lat")
    lon = node.get("lon")
    if lat is None or lon is None:
        return ""

    operator = tags.get("operator", "")
    manufacturer = tags.get("manufacturer", "")
    direction = tags.get("direction", "")
    zone = tags.get("surveillance:zone", "")
    camera_type = tags.get("camera:type", "")

    name_parts = [p for p in [operator, "ALPR"] if p]
    name = xml_escape(" ".join(name_parts)) if name_parts else "ALPR Camera"

    desc_lines = [f"OSM node: https://www.openstreetmap.org/node/{node.get('id')}"]
    for label, value in [
        ("Operator", operator),
        ("Manufacturer", manufacturer),
        ("Direction", direction),
        ("Zone", zone),
        ("Camera type", camera_type),
    ]:
        if value:
            desc_lines.append(f"{label}: {value}")
    description = xml_escape("\n".join(desc_lines))

    try:
        direction_int = int(direction)
        # Draw vision code with 40 degree FOV (should be good enough)
        left_lat, left_lon = move_point(lat,lon,100,direction_int-20)
        right_lat, right_lon = move_point(lat,lon,100,direction_int+20)
        coordsArray = [f"{lon},{lat}",f"{left_lon},{left_lat}",f"{right_lon},{right_lat}",f"{lon},{lat}"]
    except:
        # not a number for direction, assume it's 360 degree
        coordsArray = circle_points(lat,lon,100,32)

    outputCoords=""
    for p in coordsArray:
        outputCoords=f"{outputCoords}{p}\n"
    return (
        "    <Placemark>\n"
        f"      <name>{name}</name>\n"
        f"      <description>{description}</description>\n"
        "       <LineString>\n"
        "           <extrude>0</extrude>\n"
        "           <tessellate>1</tessellate>\n"
        "           <altitudeMode>clampToGround</altitudeMode>\n"
        "           <coordinates>\n"
        f"              {outputCoords}"
        "           </coordinates>\n"
        "      </LineString>\n"
        "      <styleUrl>#alprStyle</styleUrl>\n"
        "    </Placemark>\n"
    )


def write_kml_files(nodes: list, scope_label: str, output_dir: str,
                     max_per_file: int = GOOGLE_MY_MAPS_MAX_PER_LAYER) -> list:
    """
    Splits `nodes` into files of at most `max_per_file` placemarks each --
    matching Google My Maps' per-layer limit by default, so each output
    file can be imported as one clean layer without silent truncation.
    Returns the list of file paths written.
    """
    os.makedirs(output_dir, exist_ok=True)
    today = date.today().isoformat()
    safe_scope = scope_label.lower().replace(" ", "_")

    chunks = [nodes[i:i + max_per_file] for i in range(0, len(nodes), max_per_file)] or [[]]
    paths = []
    total_parts = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        suffix = f"_part{idx}of{total_parts}" if total_parts > 1 else ""
        filename = f"deflock_{safe_scope}_{today}{suffix}.kml"
        path = os.path.join(output_dir, filename)

        placemarks = "".join(node_to_placemark(n) for n in chunk)
        doc_name = f"DeFlock ALPR Cameras -- {scope_label} ({today})"
        if total_parts > 1:
            doc_name += f" -- part {idx} of {total_parts}"

        kml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
            "  <Document>\n"
            f"    <name>{xml_escape(doc_name)}</name>\n"
            "    <description>Source: OpenStreetMap (surveillance:type=ALPR), "
            "same data DeFlock.me displays. ODbL licensed -- see "
            "https://www.openstreetmap.org/copyright</description>\n"
            '<Style id="alprStyle">\n'
            "    <LineStyle>\n"
            "        <color>ffff00ff</color>\n"
            "        <colorMode>normal</colorMode>\n"
            "        <width>8</width>\n"
            "    </LineStyle>\n"
            "    <PolyStyle>\n"
            "        <color>77ff00ff</color>\n"
            "        <colorMode>normal</colorMode>\n"
            "        <fill>true</fill>\n"
            "        <outline>true</outline>\n"
            "    </PolyStyle>\n"
            "</Style>\n"
            f"{placemarks}"
            "  </Document>\n"
            "</kml>\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(kml)
        paths.append(path)
    return paths


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Pull ALPR camera locations from OpenStreetMap (DeFlock's data source) as KML."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--state", help="US state(s) name or 2-letter code(s) (e.g. 'California', 'CA', 'California,Nevada' or 'CA,NV')")
    group.add_argument("--all-us", action="store_true", help="Fetch all 50 states + DC")
    group.add_argument("--list-states", action="store_true", help="List valid --state values and exit")
    parser.add_argument("--output-dir", default=".", help="Directory to write KML file(s) into (default: current directory)")
    parser.add_argument(
        "--max-per-file", type=int, default=GOOGLE_MY_MAPS_MAX_PER_LAYER,
        help=(
            "Max placemarks per output KML file (default: 2000, matching Google "
            "My Maps' per-layer limit -- exceeding that limit gets SILENTLY "
            "truncated by Google with no error, not just slow)."
        ),
    )
    args = parser.parse_args()

    if args.list_states:
        print(list_states())
        return

    if not args.state and not args.all_us:
        parser.error("specify --state, --all-us, or --list-states")

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if args.all_us:
        print(f"Fetching all 50 states + DC from OpenStreetMap (as of {fetched_at})...")
        print("Querying state by state (not one national query) -- see script comments for why.")

        def progress(i, total, code):
            print(f"  [{i}/{total}] {code}...")

        nodes = fetch_all_us_nodes(progress_callback=progress)
        scope_label = "United States"
    else:
        for state in args.state.split(','):
            state_code = resolve_state_code(state)
            print(f"Fetching {state_code} from OpenStreetMap (as of {fetched_at})...")
            nodes = fetch_state_nodes(state_code)
            # Use the originally-typed name/code for a friendlier filename/label.
            scope_label = state

            print(f"Got {len(nodes)} ALPR camera nodes.")

            if len(nodes) == 0:
                print("Nothing to write -- zero nodes found for this scope.")
                return

            paths = write_kml_files(nodes, scope_label, args.output_dir, args.max_per_file)

            print(f"\nWrote {len(paths)} file(s):")
            for p in paths:
                print(f"  {p}")

            if len(paths) > 1:
                print(
                    f"\nNote: split into {len(paths)} files because Google My Maps silently "
                    f"truncates any single layer past {args.max_per_file} features (no error "
                    "shown -- it just drops the rest). Import each file as a separate layer. "
                    "Google My Maps also caps at 10 layers / 10,000 features per MAP total -- "
                    "if you have more than that, you'll need multiple separate My Maps maps, "
                    "or use Google Earth Pro / QGIS instead, which don't have this limit."
                )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        # Broad on purpose: known failure modes (Overpass unreachable, bad
        # --state value) raise RuntimeError/ValueError, but network code
        # has already shown it can fail in ways that don't fit the
        # exception types you'd expect (see run_overpass_query's comment
        # on http.client.RemoteDisconnected) -- this is the final backstop
        # so an end user always gets a clean message, never a raw
        # traceback, regardless of what actually went wrong.
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
