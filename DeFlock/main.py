#!/usr/bin/env python3
"""
Modified version of https://github.com/daTechGuy/deflock-kml-export that removes a lot of the manual input flags, designed to be run in a docker container with a schedule
Also modified to draw vision cones/circles around cameras for use with ALPRwatch Maps
"""

import http.client
import json
import glob
import os
import math
import pycron
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from xml.sax.saxutils import escape as xml_escape

# Chnage User-Agent when requesting from overpass API
REQUEST_HEADERS = {
    "User-Agent": "deflock-kml-export/2.0 (contact: run by end user; ALPR-mapping tool using the OpenStreetMap Overpass API)",
}
STATES = 'AK,AL,AR,AZ,CA,CO,CT,DE,FL,GA,HI,IA,ID,IL,IN,KS,KY,LA,MA,MD,ME,MI,MN,MO,MS,MT,NC,ND,NE,NH,NJ,NM,NV,NY,OH,OK,OR,PA,RI,SC,SD,TN,TX,UT,VA,VT,WA,WI,WV,WY,DC'

OVERPASS_MIRRORS = os.getenv('OVERPASS_MIRRORS', 'https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter').split(',')
REQUEST_TIMEOUT_SECONDS = int(os.getenv('REQUEST_TIMEOUT_SECONDS',180))
RETRIES_PER_MIRROR = int(os.getenv('RETRIES_PER_MIRROR',5))
RETRY_BACKOFF_SECONDS = int(os.getenv('RETRY_BACKOFF_SECONDS',30))
CAMERA_VISION_RANGE = int(os.getenv('CAMERA_VISION_RANGE',100))
CAMERA_VISION_FOV = int(os.getenv('CAMERA_VISION_FOV',40))
INCLUDED_STATES = os.getenv('INCLUDED_STATES',STATES).split(',')
OUTPUT_PATH = os.getenv('OUTPUT_PATH','.')
SCHEDULE_CRONTIME = os.getenv('SCHEDULE_CRONTIME','')

def run_overpass_query(query: str, label: str) -> dict:
    """
    POST a query to Overpass, retrying across mirrors. Returns parsed JSON.
    """
    for mirror in OVERPASS_MIRRORS:
        for attempt in range(0, RETRIES_PER_MIRROR):
            try:
                with urllib.request.urlopen(urllib.request.Request(mirror, data=urllib.parse.urlencode({"data": query}).encode("utf-8"), method="POST", headers=REQUEST_HEADERS), timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    response_json = json.loads(resp.read())
                    if "elements" not in response_json:
                        print(f"[{label}] Unexpected response from mirror {mirror}", file=sys.stderr)
                    else:
                        return response_json
            except urllib.error.HTTPError as e:
                print(f"[{label}] HTTP {e.code} Error from mirror {mirror}", file=sys.stderr)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                    http.client.HTTPException, ConnectionError, OSError) as e:
                print(f"[{label}] {str(e)} Error from mirror {mirror}", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_SECONDS)
    print(f"[{label}] all Overpass mirrors failed.", file=sys.stderr)
    return False


# ============================================================================
# Fetching ALPR nodes
# ============================================================================

def fetch_state_nodes(state_code: str) -> list:
    """
    Fetch every surveillance:type=ALPR node inside one US state's boundary.
    """
    if state_code in STATES.split(','):
        query = (
            "[out:json][timeout:150][maxsize:1073741824];"
            f'area["ISO3166-2"="US-{state_code}"]["admin_level"="4"]->.searchArea;'
            'node["surveillance:type"="ALPR"](area.searchArea);'
            "out body;"
        )
        result = run_overpass_query(query, state_code)
        if result:
            return result.get("elements", [])
        else:
            return False
    else:
        return False


# ============================================================================
# KML generation
# ============================================================================

def move_point(lat: float, lon: float, distance: int, bearing: int):
    """
    Returns (lat, lon) a `distance` meters away at `bearing` degrees.
    0 = north, 90 = east, 270 = west.
    """

    # Convert distance meters to angular distance using Earth's equatorial radius
    distance_ang =  distance / 6378137
    
    # Convert inputs to radians
    lat_rad,lon_rad,bearing_rad = math.radians(lat),math.radians(lon),math.radians(bearing)
    
    # Calculate new latitude
    new_lat_rad = math.asin(
        math.sin(lat_rad) * math.cos(distance_ang) +
        math.cos(lat_rad) * math.sin(distance_ang) * math.cos(bearing_rad)
    )
    
    # Calculate new longitude
    new_lon_rad = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(distance_ang) * math.cos(lat_rad),
        math.cos(distance_ang) - math.sin(lat_rad) * math.sin(new_lat_rad)
    )
    
    # Return values back as degrees 
    return math.degrees(new_lat_rad), math.degrees(new_lon_rad)

def draw_non_directional_vision(center_lat: float, center_lon: float, radius: int, num_points: int):
    """
    Draws a circle around node location (360 degree camera)
    """
    points = []
    for i in range(num_points):
        lat, lon = move_point(center_lat, center_lon, radius, (360/num_points)*i)
        points.append(f"{lon},{lat}")
    # Close circle by appending first position to end
    points.append(points[0])
    return points

def draw_directional_vision(lat: float, lon: float, direction: int):
    """
    Draws a vision cone out from node location (directional camera)
    """
    left_lat, left_lon = move_point(lat,lon,CAMERA_VISION_RANGE,direction-(CAMERA_VISION_FOV/2))
    right_lat, right_lon = move_point(lat,lon,CAMERA_VISION_RANGE,direction+(CAMERA_VISION_FOV/2))
    # Return triangle of vision cone (including origin point twice to close triangle)
    return [f"{lon},{lat}",f"{left_lon},{left_lat}",f"{right_lon},{right_lat}",f"{lon},{lat}"]

def node_to_placemark(node: dict) -> str:
    """
    Converts OSM node to XML placemark for KML
    """

    tags = node.get("tags", {})
    lat,lon = node.get("lat"),node.get("lon")

    # Return nothing if node does not have a valid location
    if lat is None or lon is None:
        return ""

    operator,manufacturer,direction,zone,camera_type = tags.get("operator", ""),tags.get("manufacturer", ""),tags.get("direction", ""),tags.get("surveillance:zone", ""),tags.get("camera:type", "")

    # Set name for placemark, fallback to ALPR Camera
    name_parts = [p for p in [operator, "ALPR"] if p]
    name = xml_escape(" ".join(name_parts)) if name_parts else "ALPR Camera"

    # Build description from tags
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

    if isinstance(direction,int):
        points = draw_directional_vision(lat,lon,int(direction)) 
    else:
        points = draw_non_directional_vision(lat,lon,CAMERA_VISION_RANGE,32)

    # Convert points array to plain string for inputting in XML
    coordinates = "".join(f"{p}\n" for p in points)
    return (
        "    <Placemark>\n"
        f"      <name>{name}</name>\n"
        f"      <description>{description}</description>\n"
        "       <LineString>\n"
        "           <extrude>0</extrude>\n"
        "           <tessellate>1</tessellate>\n"
        "           <altitudeMode>clampToGround</altitudeMode>\n"
        "           <coordinates>\n"
        f"              {coordinates}"
        "           </coordinates>\n"
        "      </LineString>\n"
        "      <styleUrl>#alprStyle</styleUrl>\n"
        "    </Placemark>\n"
    )


def write_kml_file(nodes: list, state: str):
    """
    Writes nodes to file with todays date/state
    """
    os.makedirs(OUTPUT_PATH, exist_ok=True)
    today = date.today().isoformat()

    # Delete older files for state
    for f in glob.glob(os.path.join(OUTPUT_PATH,f"deflock_{state}_*.kml")):
        print(f"[{state}] deleting old file: {f}", file=sys.stdout)
        os.remove(f)

    path = os.path.join(OUTPUT_PATH, f"deflock_{state}_{today}.kml")

    placemarks = "".join(node_to_placemark(n) for n in nodes)
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
    print(f"[{state}] wrote file: {path}", file=sys.stdout)
    return

if len(SCHEDULE_CRONTIME):
    while True:
        if pycron.is_now(SCHEDULE_CRONTIME):
            for state in INCLUDED_STATES:
                nodes = fetch_state_nodes(state)
                if len(nodes) > 0:
                    write_kml_file(nodes, state)
                else:
                    print(f"[{state}] returned no nodes, nothing to write", file=sys.stderr)
            # Sleep to avoid being triggered multiple times in the same minute
            # This shouldn't happen anyway as the process almost always takes >1 minute
            time.sleep(60)
        else:
            time.sleep(60)  
else:
    for state in INCLUDED_STATES:
        nodes = fetch_state_nodes(state)
        if len(nodes) > 0:
            write_kml_file(nodes, state)
        else:
            print(f"[{state}] returned no nodes, nothing to write", file=sys.stderr)
    exit(0)