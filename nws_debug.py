import requests, yaml

cfg = yaml.safe_load(open("config/config.yaml"))["weather"]
lat, lon, ua = cfg["lat"], cfg["lon"], cfg["user_agent"]
print("User-Agent:", repr(ua), "| lat/lon:", lat, lon)
h = {"User-Agent": ua, "Accept": "application/geo+json"}

def get(url):
    r = requests.get(url, headers=h, timeout=15)
    print(r.status_code, r.reason, url)
    return r

r = get(f"https://api.weather.gov/points/{lat},{lon}")
if r.ok:
    p = r.json()["properties"]
    get(p["forecast"])
    get(p["forecastHourly"])
    s = get(p["observationStations"])
    if s.ok:
        st = s.json()["features"][0]["properties"]["stationIdentifier"]
        print("first station:", st)
        get(f"https://api.weather.gov/stations/{st}/observations/latest")