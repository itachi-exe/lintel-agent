-- Register the NYC open records Lintel reads as Zetaris Lightning REST tables.
-- Run once against your Zetaris instance (Gateway SQL editor, or
-- scripts/run_sql.py from github.com/zetaris/getting-started over REST or JDBC).
-- After this, app/zetaris.py sends every agent query here instead of Socrata.

CREATE LIGHTNING DATABASE NYC DESCRIBE BY "NYC Open Data (Socrata) records Lintel reads";

CREATE LIGHTNING REST TABLE hpd_violations FROM NYC REQUEST(
    endpoint "https://data.cityofnewyork.us/resource/wvxf-dwi5.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
    "User-Agent" "Lintel github.com/itachi-exe/lintel-agent"
) BODY ();

CREATE LIGHTNING REST TABLE sr311 FROM NYC REQUEST(
    endpoint "https://data.cityofnewyork.us/resource/erm2-nwe9.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
    "User-Agent" "Lintel github.com/itachi-exe/lintel-agent"
) BODY ();

CREATE LIGHTNING REST TABLE dob_violations FROM NYC REQUEST(
    endpoint "https://data.cityofnewyork.us/resource/3h2n-5cm9.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
    "User-Agent" "Lintel github.com/itachi-exe/lintel-agent"
) BODY ();

CREATE LIGHTNING REST TABLE pluto FROM NYC REQUEST(
    endpoint "https://data.cityofnewyork.us/resource/64uk-42ks.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
    "User-Agent" "Lintel github.com/itachi-exe/lintel-agent"
) BODY ();

CREATE LIGHTNING REST TABLE flood_2050 FROM NYC REQUEST(
    endpoint "https://data.cityofnewyork.us/resource/27ya-gqtm.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
    "User-Agent" "Lintel github.com/itachi-exe/lintel-agent"
) BODY ();

-- Smoke test
SELECT violationid, class, inspectiondate FROM nyc.hpd_violations WHERE bbl = '5004900004' LIMIT 3;
