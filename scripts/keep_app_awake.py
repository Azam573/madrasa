"""
scripts/keep_app_awake.py — Streamlit Community Cloud ফ্রি-টিয়ারে অ্যাপ ১২ ঘণ্টা
কোনো ভিজিটর না পেলে ঘুমিয়ে পড়ে (একটা "Zzzz..." পেজ দেখায়, যেখানে
"Yes, get this app back up!" বাটনে ক্লিক করলে তবেই আবার চালু হয়)।

শুধু plain HTTP GET/curl দিয়ে এই বাটন ক্লিক হয় না — তাই headless ব্রাউজার
দিয়ে অ্যাপ ভিজিট করে, ঘুমিয়ে থাকলে বাটনটা খুঁজে ক্লিক করা হয়। GitHub
Actions cron থেকে প্রতি ৬ ঘণ্টায় চালানো হয় (১২-ঘণ্টার সীমার নিরাপদ মার্জিন
রেখে)।
"""
import re
import sys
from playwright.sync_api import sync_playwright

APP_URL = "https://madrasaerp.streamlit.app/"
WAKE_BUTTON_PATTERN = re.compile("get this app back up", re.IGNORECASE)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)

        wake_button = page.get_by_role("button", name=WAKE_BUTTON_PATTERN)
        if wake_button.count() > 0:
            print("App is asleep -- clicking wake-up button...")
            wake_button.first.click()
            page.wait_for_timeout(30000)
            print("Wake triggered.")
        else:
            print("App already awake.")

        browser.close()


if __name__ == "__main__":
    main()
