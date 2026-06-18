from playwright.sync_api import sync_playwright
from urllib.parse import urlparse
import json
import random
import re
import time
import os

CHROME_PROFILE = os.getenv("CHROME_PROFILE")
USERNAME = os.getenv("INSTAGRAM_USERNAME")

MAX_UNSAVES = 999999
MAX_SCROLL_ROUNDS = 300
MAX_NO_NEW_ROUNDS = 12
RETRY_PER_POST = 6
RETURN_TO_SAVED_EVERY = 4
NAV_RETRY_COUNT = 3

MIN_WAIT = 0.6
MAX_WAIT = 1.6
POST_ACTION_WAIT = 1.0

SCROLL_AMOUNT_MIN = 1200
SCROLL_AMOUNT_MAX = 2400

BTN_TEXT_RE = re.compile(r"(saved|unsave|remove|save|bookmark)", re.I)
COLLECTION_ACTION_RE = re.compile(r"(remove|done|save|unsave)", re.I)


def wait_and_print(min_s=MIN_WAIT, max_s=MAX_WAIT):
    delay = random.uniform(min_s, max_s)
    print(f"wait: {delay:.2f}s")
    time.sleep(delay)


def print_unsaved_count(count):
    print(f"unsaved: {count}")


def normalize_post_href(href):
    if not href:
        return None
    href = href.split("?")[0].split("#")[0]
    path = urlparse(href).path if href.startswith("http") else href
    path = path.rstrip("/")
    for needle in ("/p/", "/reel/", "/reels/"):
        idx = path.find(needle)
        if idx != -1:
            return path[idx:]
    return None


def load_progress():
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("processed", []))
    except Exception:
        return set()


def save_progress(processed):
    payload = {"processed": sorted(processed), "updated_at": int(time.time())}
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def open_saved_page(page):
    if not goto_with_retry(page, f"https://www.instagram.com/{USERNAME}/saved/all-posts/"):
        return False
    time.sleep(2.0)
    try:
        page.wait_for_selector("article", timeout=10000)
    except Exception:
        pass
    return True


def scroll_saved_page(page):
    amount = random.randint(SCROLL_AMOUNT_MIN, SCROLL_AMOUNT_MAX)
    page.mouse.wheel(0, amount)
    wait_and_print()


def extract_visible_saved_links(page):
    selectors = [
        "article a[href*='/p/'], article a[href*='/reel/'], article a[href*='/reels/']",
        "a[href*='/p/'], a[href*='/reel/'], a[href*='/reels/']",
    ]
    links = []
    for selector in selectors:
        loc = page.locator(selector)
        try:
            for i in range(loc.count()):
                href = normalize_post_href(loc.nth(i).get_attribute("href"))
                if href:
                    links.append(href)
        except Exception:
            pass
    return links


def goto_with_retry(page, url):
    for attempt in range(1, NAV_RETRY_COUNT + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return True
        except Exception:
            if attempt == NAV_RETRY_COUNT:
                return False
            time.sleep(1.2 * attempt)
    return False


def has_icon(page, pattern):
    sel = f"svg[aria-label*='{pattern}' i], [aria-label*='{pattern}' i]"
    try:
        loc = page.locator(sel)
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def infer_saved_state(page):
    if has_icon(page, "Saved") or has_icon(page, "Remove") or has_icon(page, "Unsave"):
        return True
    if has_icon(page, "Save"):
        return False
    return None


def click_icon_parent_button(page):
    icon_selectors = [
        "svg[aria-label*='Saved' i]",
        "svg[aria-label*='Remove' i]",
        "svg[aria-label*='Unsave' i]",
        "svg[aria-label*='Bookmark' i]",
        "svg[aria-label*='Save' i]",
    ]
    for sel in icon_selectors:
        try:
            icon = page.locator(sel)
            if icon.count() == 0 or not icon.first.is_visible():
                continue
            try:
                icon.first.click(force=True)
                return True
            except Exception:
                btn = icon.first.locator("xpath=ancestor::button[1]")
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(force=True)
                    return True
        except Exception:
            pass
    return False


def click_save_button_from_action_bar(page):
    try:
        by_role = page.get_by_role("button", name=BTN_TEXT_RE)
        if by_role.count() > 0 and by_role.first.is_visible():
            by_role.first.click(force=True)
            return True
    except Exception:
        pass

    if click_icon_parent_button(page):
        return True

    try:
        bars = page.locator("article section")
        for b in range(bars.count()):
            sec = bars.nth(b)
            btns = sec.locator("button")
            visible_btns = []
            for i in range(btns.count()):
                if btns.nth(i).is_visible():
                    visible_btns.append(btns.nth(i))
            if visible_btns:
                visible_btns[-1].click(force=True)
                return True
    except Exception:
        pass

    return False


def handle_collection_popup(page):
    # If Instagram opens a collection modal, this tries to finalize "unsave".
    dialog_selectors = ["div[role='dialog']", "div[aria-modal='true']"]
    dialog = None
    for sel in dialog_selectors:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible():
                dialog = loc.first
                break
        except Exception:
            pass

    if dialog is None:
        return False

    # Try direct action buttons first (Remove/Done/Save/Unsave).
    try:
        action_btn = dialog.get_by_role("button", name=COLLECTION_ACTION_RE)
        if action_btn.count() > 0 and action_btn.first.is_visible():
            action_btn.first.click(force=True)
            return True
    except Exception:
        pass

    # Fallback: toggle selected collection items off, then click a footer action.
    try:
        toggles = dialog.locator(
            "button[aria-pressed='true'], input[type='checkbox']:checked, "
            "svg[aria-label*='checked' i], [aria-label*='Selected' i]"
        )
        for i in range(min(toggles.count(), 20)):
            try:
                item = toggles.nth(i)
                if item.is_visible():
                    item.click(force=True)
                    time.sleep(0.15)
            except Exception:
                pass
    except Exception:
        pass

    try:
        done_btn = dialog.get_by_role("button", name=COLLECTION_ACTION_RE)
        if done_btn.count() > 0 and done_btn.first.is_visible():
            done_btn.first.click(force=True)
            return True
    except Exception:
        pass

    return False


def unsave_current_open_post(page):
    state_before = infer_saved_state(page)
    if state_before is False:
        return False

    clicked = click_save_button_from_action_bar(page)
    if not clicked:
        return False

    time.sleep(POST_ACTION_WAIT)
    handle_collection_popup(page)
    time.sleep(0.4)
    state_after = infer_saved_state(page)
    if state_after is False:
        return True

    time.sleep(0.9)
    return infer_saved_state(page) is False


def process_link_unsave(page, href):
    full_url = "https://www.instagram.com" + href
    if not goto_with_retry(page, full_url):
        return False

    wait_and_print()
    for _ in range(RETRY_PER_POST):
        if unsave_current_open_post(page):
            return True
        time.sleep(0.5)
    return False


def run_unsave(page):
    processed = load_progress()
    seen = set(processed)
    unsaved = 0
    no_new_rounds = 0
    processed_since_return = 0

    if not open_saved_page(page):
        return

    for _ in range(MAX_SCROLL_ROUNDS):
        if unsaved >= MAX_UNSAVES:
            break

        visible = extract_visible_saved_links(page)
        new_links = []
        for href in visible:
            if href not in seen:
                seen.add(href)
                new_links.append(href)

        if not new_links:
            no_new_rounds += 1
        else:
            no_new_rounds = 0

        for href in new_links:
            if unsaved >= MAX_UNSAVES:
                break
            if href in processed:
                continue

            if process_link_unsave(page, href):
                unsaved += 1
                print_unsaved_count(unsaved)

            processed.add(href)
            save_progress(processed)
            wait_and_print()
            processed_since_return += 1
            if processed_since_return >= RETURN_TO_SAVED_EVERY:
                if not open_saved_page(page):
                    return
                processed_since_return = 0

        if no_new_rounds >= MAX_NO_NEW_ROUNDS:
            break

        if processed_since_return > 0:
            if not open_saved_page(page):
                return
            processed_since_return = 0

        scroll_saved_page(page)
        if not new_links:
            scroll_saved_page(page)


def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=CHROME_PROFILE,
            channel="chrome",
            headless=False,
            viewport={"width": 1400, "height": 1000},
        )

        page = context.pages[0] if context.pages else context.new_page()
        goto_with_retry(page, "https://www.instagram.com/")
        time.sleep(3.0)

        input("Press ENTER to start unsaving...")
        run_unsave(page)
        input("Press ENTER to close browser...")
        context.close()


if __name__ == "__main__":
    main()
