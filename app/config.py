"""Where every record comes from. All public NYC endpoints, no keys required."""

from __future__ import annotations

import os

SOCRATA = "https://data.cityofnewyork.us/resource/{ds}.json"
GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/search"
TRANSIT = "https://api.transitous.org/api/v1/plan"

# Socrata dataset ids on NYC Open Data
DS = {
    "hpd_violations": "wvxf-dwi5",
    "hpd_complaints": "uwyv-629c",
    "311": "erm2-nwe9",
    "dob_violations": "3h2n-5cm9",
    "pluto": "64uk-42ks",
    "flood_2050": "27ya-gqtm",
}

_ROW = "https://data.cityofnewyork.us/resource/{ds}.json?{key}={{{field}}}"

# Record page templates. Row links open the exact Socrata row the answer read.
LINK = {
    "hpd_violation": _ROW.format(ds=DS["hpd_violations"], key="violationid", field="id"),
    "sr311": _ROW.format(ds=DS["311"], key="unique_key", field="id"),
    "dob_violation": _ROW.format(ds=DS["dob_violations"], key="isn_dob_bis_viol", field="id"),
    "pluto": "https://zola.planning.nyc.gov/bbl/{bbl}",
    "lot": "https://zola.planning.nyc.gov/bbl/{bbl}",
    "zola": "https://zola.planning.nyc.gov/bbl/{bbl}",
    "hpd_building": "https://data.cityofnewyork.us/resource/" + DS["hpd_violations"] + ".json?bbl={bbl}",
    "dob_building": "https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin={bin}",
    "flood": "https://data.cityofnewyork.us/d/" + DS["flood_2050"],
    "transit": "https://transitous.org",
}

BOROUGHS = {"1": "Manhattan", "2": "Bronx", "3": "Brooklyn", "4": "Queens", "5": "Staten Island"}

HTTP_TIMEOUT = float(os.environ.get("LINTEL_HTTP_TIMEOUT", "30"))
USER_AGENT = "lintel-agent/2 (+https://github.com/; open agent hackathon 2026)"
