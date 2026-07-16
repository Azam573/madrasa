"""
tests/_streamlit_page_smoke.py — helper script, not a pytest file

Loads app.py in a single fresh AppTest instance, logs in, visits ONE page,
and prints "OK" or "FAIL: <message>". Run as a subprocess (one process per
page) from test_streamlit_smoke.py so each page visit gets a completely
clean Python/Streamlit interpreter state — AppTest's internal widget-state
tracking was found to accumulate stale entries across many sequential
reruns on one long-lived instance (a testing-harness limitation, not a
product bug — the real app works fine, verified manually via a real
browser), and process-per-page isolation sidesteps that entirely.

Usage: python _streamlit_page_smoke.py <username> <password> <page_name>
Requires DATABASE_URL to already point at a migrated, seeded DB.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    username, password, page_name = sys.argv[1], sys.argv[2], sys.argv[3]

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(os.path.join(ROOT, "app.py"))
    at.run(timeout=60)
    if at.exception:
        print(f"FAIL: login page raised: {at.exception}")
        return 1

    at.text_input[0].set_value(username)
    at.text_input[1].set_value(password)
    at.button[0].click().run(timeout=30)
    if at.exception:
        print(f"FAIL: login raised: {at.exception}")
        return 1

    at.session_state["nav_page"] = page_name
    at.run(timeout=45)
    if at.exception:
        print(f"FAIL: {at.exception}")
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
