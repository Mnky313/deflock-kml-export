#!/usr/bin/env python3
"""
Modified version of https://github.com/daTechGuy/deflock-kml-export that removes a lot of the manual input flags, designed to be run in a docker container with a schedule
Also modified to draw vision cones/circles around cameras for use with ALPRwatch Maps
"""

import http.client
import json
import os
import math
import schedule
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from xml.sax.saxutils import escape as xml_escape

REQUEST_HEADERS = {
    "User-Agent": "deflock-kml-export/2.0 (contact: run by end user; ALPR-mapping tool using the OpenStreetMap Overpass API)",
}

OVERPASS_MIRRORS = os.getenv('INCLUDED_STATES').split(',')
REQUEST_TIMEOUT_SECONDS = int(os.getenv('REQUEST_TIMEOUT_SECONDS'))
RETRIES_PER_MIRROR = int(os.getenv('RETRIES_PER_MIRROR'))
RETRY_BACKOFF_SECONDS = int(os.getenv('RETRY_BACKOFF_SECONDS'))
CAMERA_VISION_RANGE = int(os.getenv('CAMERA_VISION_RANGE'))
CAMERA_VISION_FOV = int(os.getenv('CAMERA_VISION_FOV'))
INCLUDED_STATES = os.getenv('INCLUDED_STATES').split(',')
OUTPUT_PATH = os.getenv('OUTPUT_PATH')


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
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                    http.client.HTTPException, ConnectionError, OSError) as e:
                last_error = str(e)
            print(f"  [{label}] attempt {attempt} via {mirror} failed: {last_error}", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise RuntimeError(f"[{label}] all Overpass mirrors failed. Last error: {last_error}")


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


# ============================================================================
# KML generation
# ============================================================================

def move_point(lat: float, lng: float, distance: int, bearing: int):
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

def circle_points(center_lat: float, center_lng: float, radius: int, num_points: int):
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
        left_lat, left_lon = move_point(lat,lon,CAMERA_VISION_RANGE,direction_int-(CAMERA_VISION_FOV/2))
        right_lat, right_lon = move_point(lat,lon,CAMERA_VISION_RANGE,direction_int+(CAMERA_VISION_FOV/2))
        coordsArray = [f"{lon},{lat}",f"{left_lon},{left_lat}",f"{right_lon},{right_lat}",f"{lon},{lat}"]
    except:
        # not a number for direction, assume it's 360 degree
        coordsArray = circle_points(lat,lon,CAMERA_VISION_RANGE,32)

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


def write_kml_files(nodes: list, state: str, output_dir: str):
    """
    Writes nodes to file with todays date/state
    """
    os.makedirs(output_dir, exist_ok=True)
    today = date.today().isoformat()

    filename = f"deflock_{state}_{today}.kml"
    path = os.path.join(output_dir, filename)

    placemarks = "".join(node_to_placemark(n) for n in chunk)
    doc_name = f"DeFlock ALPR Cameras -- {state} ({today})"

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
    return path

def main():
    for state in INCLUDED_STATES:
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"Fetching {state} from OpenStreetMap (as of {fetched_at})...")
        nodes = fetch_state_nodes(state)

        print(f"Got {len(nodes)} ALPR camera nodes.")

        if len(nodes) == 0:
            print("Nothing to write -- zero nodes found for this scope.")
            return

        path = write_kml_files(nodes, state, OUTPUT_PATH)

        print(f"\nWrote file: {path}")

schedule.every().day.at("20:50").do(main)

while True:
    schedule.run_pending()
    time.sleep(60)

