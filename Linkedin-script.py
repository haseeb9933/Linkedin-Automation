import os
import time
import random
import traceback
from flask import Flask, jsonify, request
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# ------------------------------------------------------------------
# ENV
# ------------------------------------------------------------------
load_dotenv()

LINKEDIN_SESSION_ID = os.getenv("LINKEDIN_SESSION_ID")
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", 5000))
HEADLESS = os.getenv("HEADLESS", "0") == "1"

DEBUG_DIR = "debug_dumps"
os.makedirs(DEBUG_DIR, exist_ok=True)

# ------------------------------------------------------------------
# GLOBALS
# ------------------------------------------------------------------
app = Flask(__name__)
driver = None
wait = None

# ------------------------------------------------------------------
# SELECTORS (LINKEDIN 2025 SAFE)
# ------------------------------------------------------------------
POST_CONTAINER_XPATHS = [
    "//div[@data-urn and .//span[contains(@class,'update-components-actor__name')]]",
    "//div[@data-urn and .//a[contains(@href,'/in/')]]",
    "//div[@data-urn]"
]

AUTHOR_XPATHS = [
    ".//span[contains(@class,'update-components-actor__name')]",
    ".//a[contains(@href,'/in/')][1]"
]

TEXT_XPATHS = [
    ".//div[contains(@class,'update-components-text')]//span[@dir='ltr']",
    ".//span[@dir='ltr']"
]

# ------------------------------------------------------------------
# UTILS
# ------------------------------------------------------------------
def dump_debug(tag):
    if not driver:
        return
    ts = int(time.time())
    with open(f"{DEBUG_DIR}/{tag}_{ts}.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    driver.save_screenshot(f"{DEBUG_DIR}/{tag}_{ts}.png")


def wait_for_any(xpaths, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        for xp in xpaths:
            if driver.find_elements(By.XPATH, xp):
                return True
        time.sleep(0.5)
    return False


def human_scroll(passes=2):
    for _ in range(passes):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.4);")
        time.sleep(random.uniform(1.5, 2.5))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(1.5, 2.5))


# ------------------------------------------------------------------
# BROWSER INIT (HEADLESS-SAFE)
# ------------------------------------------------------------------
def initialize_browser():
    global driver, wait

    if driver:
        return True

    if not LINKEDIN_SESSION_ID:
        print("❌ LINKEDIN_SESSION_ID missing")
        return False

    try:
        print("🚀 Starting Chrome via Selenium…")

        options = webdriver.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-notifications")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")

        if HEADLESS:
            options.add_argument("--headless=new")

        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 25)

        # Inject cookie
        driver.get("https://www.linkedin.com/")
        time.sleep(2)
        driver.add_cookie({
            "name": "li_at",
            "value": LINKEDIN_SESSION_ID,
            "domain": ".linkedin.com",
            "path": "/",
            "secure": True,
            "httpOnly": True
        })

        driver.get("https://www.linkedin.com/feed/")
        time.sleep(3)

        LOGIN_OK = [
            "//input[contains(@aria-label,'Search')]",
            "//button[contains(@aria-label,'Start a post')]",
            "//*[contains(text(),'Start a post')]"
        ]

        LOGIN_PAGE = [
            "//input[@name='session_key']",
            "//input[@name='session_password']"
        ]

        CHECKPOINT = [
            "//*[contains(translate(.,'VERIFY','verify'),'verify')]",
            "//*[contains(translate(.,'CHALLENGE','challenge'),'challenge')]"
        ]

        if wait_for_any(LOGIN_OK):
            print("✅ LinkedIn logged in")
            return True

        if wait_for_any(CHECKPOINT, 3):
            dump_debug("checkpoint")
            return False

        if wait_for_any(LOGIN_PAGE, 3):
            dump_debug("login_page")
            return False

        dump_debug("unknown_state")
        return False

    except Exception:
        traceback.print_exc()
        dump_debug("init_failed")
        return False


# ------------------------------------------------------------------
# SCRAPER
# ------------------------------------------------------------------
def scrape_hashtag(hashtag, max_posts):
    url = f"https://www.linkedin.com/feed/hashtag/{hashtag}/?sortBy=RECENT"
    driver.get(url)
    time.sleep(3)
    human_scroll(2)

    chosen_xpath = next(
        (xp for xp in POST_CONTAINER_XPATHS if driver.find_elements(By.XPATH, xp)),
        None
    )

    if not chosen_xpath:
        dump_debug("no_posts")
        return []

    posts, seen = [], set()

    while len(posts) < max_posts:
        for card in driver.find_elements(By.XPATH, chosen_xpath):
            urn = card.get_attribute("data-urn")
            if not urn or urn in seen:
                continue
            seen.add(urn)

            author = next(
                (el.text.strip() for xp in AUTHOR_XPATHS
                 for el in card.find_elements(By.XPATH, xp) if el),
                ""
            )

            text = next(
                (el.text.strip() for xp in TEXT_XPATHS
                 for el in card.find_elements(By.XPATH, xp) if el),
                ""
            )

            posts.append({
                "author": author,
                "text": text,
                "url": f"https://www.linkedin.com/feed/update/{urn}/"
            })

            if len(posts) >= max_posts:
                break

        human_scroll(1)

    return posts[:max_posts]


# ------------------------------------------------------------------
# API ROUTES
# ------------------------------------------------------------------
@app.route("/scrape", methods=["POST"])
def scrape():
    if not initialize_browser():
        return jsonify({"error": "browser init failed"}), 500

    data = request.get_json() or {}

    hashtag = str(data.get("hashtag", "hiring")).lstrip("#").strip()

    try:
        max_posts = int(data.get("max_posts", 5))
    except:
        max_posts = 5

    # HARD SAFETY LIMIT
    max_posts = max(1, min(max_posts, 25))

    results = scrape_hashtag(hashtag, max_posts)

    if not results:
        return jsonify({"error": "no posts found"}), 404

    return jsonify(results), 200


@app.route("/comment", methods=["POST"])
def comment():
    if not initialize_browser():
        return jsonify({"error": "browser init failed"}), 500

    data = request.get_json()
    driver.get(data["post_url"])
    time.sleep(3)

    try:
        btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(@aria-label,'Comment')]")
        ))
        btn.click()

        box = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//div[contains(@data-placeholder,'Add a comment')]")
        ))

        for ch in data["comment"]:
            box.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.15))

        post = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(@class,'comments-comment-box__submit-button')]")
        ))
        post.click()

        return jsonify({"status": "success"}), 200

    except TimeoutException:
        dump_debug("comment_failed")
        return jsonify({"status": "failed"}), 500


# ------------------------------------------------------------------
if __name__ == "__main__":
    print(f"🚀 API starting on http://{API_HOST}:{API_PORT}")
    app.run(host=API_HOST, port=API_PORT, debug=True, threaded=False)
