"""Server-seitiger Dashboard-Test. Per SCP auf Server kopieren und ausfuehren."""
import sys, os, asyncio
sys.path.insert(0, os.getcwd())
os.makedirs("logs", exist_ok=True)

async def _get(session, url, expected, cookie_jar=None):
    """GET request mit aiohttp, gibt (status_code, ok_bool) zurueck."""
    import aiohttp
    try:
        async with session.get(url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            status = "OK" if resp.status in expected else "FAIL"
            return resp.status, status
    except Exception as e:
        return str(e), "ERR"

async def test_routes():
    import aiohttp
    base = "http://localhost:8080"
    async with aiohttp.ClientSession() as session:
        # Oeffentlich (kein Auth noetig)
        tests = [
            ("/auth/login", [200]),
            ("/api/health", [200, 503]),
            ("/static/style.css", [200]),
            ("/static/htmx.min.js", [200]),
            ("/static/themes.css", [200]),
        ]
        # Geschuetzt (sollte redirect 302/303/307). "/" ist seit D9 die oeffentliche
        # Landing (allow_anon, 200) -> NICHT hier; /dashboard + /home sind protected.
        protected_pages = ["/dashboard", "/home", "/system", "/security", "/config", "/search",
                          "/errors", "/changelog", "/marshal-bot", "/server/satisfactory"]
        for route in protected_pages:
            tests.append((route, [302, 303, 307]))

        # API ohne Auth (sollte 401)
        api_routes = ["/api/analytics/system", "/api/analytics/summary",
                     "/api/forecast", "/api/backup/cloud-status",
                     "/api/theme", "/api/security/ip-overview"]
        for route in api_routes:
            tests.append((route, [401]))

        print("=== Dashboard Route Tests (ohne Auth) ===")
        failed = 0
        for route, expected in tests:
            code, status = await _get(session, base + route, expected)
            if status != "OK":
                failed += 1
            print(f"  {status} {code} {route} (erwartet {expected})")
        print(f"\n  Ergebnis: {len(tests) - failed}/{len(tests)} OK, {failed} FAIL")

async def test_auth():
    """Testet authentifizierte Routes mit selbst generiertem JWT."""
    import aiohttp
    import jwt as pyjwt
    from dotenv import load_dotenv
    from datetime import datetime, timedelta, timezone

    load_dotenv("config/.env")
    secret = os.getenv("WEB_SECRET_KEY", "")
    if not secret or secret == "CHANGE_ME_INSECURE_DEFAULT_KEY":
        print("\nAuth-Test: SKIP (WEB_SECRET_KEY nicht gesetzt oder unsicher)")
        return

    # JWT direkt generieren (umgeht Login-Flow)
    payload = {
        "sub": "local:admin",
        "username": "admin",
        "avatar": "",
        "auth_method": "test",
        "is_owner": True,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = pyjwt.encode(payload, secret, algorithm="HS256")

    base = "http://localhost:8080"
    jar = aiohttp.CookieJar()
    # Manuell Cookie setzen
    from http.cookies import SimpleCookie
    cookie = SimpleCookie()
    cookie["dashboard_token"] = token
    cookie["dashboard_token"]["path"] = "/"
    cookie["dashboard_token"]["domain"] = "localhost"

    print("\n=== Authentifizierte Route Tests ===")
    failed = 0
    async with aiohttp.ClientSession(cookies={"dashboard_token": token}) as session:
        auth_tests = [
            ("/", [200]),
            ("/dashboard", [200]),
            ("/home", [200]),
            ("/system", [200]),
            ("/security", [200]),
            ("/config", [200]),
            ("/search", [200]),
            ("/errors", [200]),
            ("/changelog", [200]),
            ("/marshal-bot", [200]),
            ("/server/satisfactory", [200]),
            ("/server/mc_bmc", [200]),
            # Vanilla wurde am 2026-08-14 stillgelegt. Die Detailseite leitet
            # bei unbekannter Server-ID auf die Startseite um (302) — bewusst
            # keine 404-Fehlerseite, weil es eine Seiten-URL ist und kein
            # API-Aufruf. Die HTMX-Teilansichten derselben Route antworten
            # dagegen mit 404.
            ("/server/mc_vanilla", [302]),
            ("/api/analytics/summary", [200]),
            ("/api/health", [200, 503]),
            ("/api/theme", [200]),
            ("/api/forecast", [200]),
            ("/api/backup/cloud-status", [200]),
        ]
        for route, expected in auth_tests:
            code, status = await _get(session, base + route, expected)
            if status != "OK":
                failed += 1
            print(f"  {status} {code} {route}")
        print(f"\n  Ergebnis: {len(auth_tests) - failed}/{len(auth_tests)} OK, {failed} FAIL")

if __name__ == "__main__":
    asyncio.run(test_routes())
    asyncio.run(test_auth())
