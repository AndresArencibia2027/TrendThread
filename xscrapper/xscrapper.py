from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import re
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    pd = None

def setup_chrome():
    chrome_options = Options()
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def load_x_trending(driver):
    """Load X.com trending page and wait for main content."""
    driver.get("https://x.com/i/jf/global-trending/home")
    WebDriverWait(driver, 120).until(
        EC.presence_of_element_located((
            By.CSS_SELECTOR,
            "div.jf-element.grid.grid-cols-1.grid-rows-1.w-full.h-full"
        ))
    )
    time.sleep(2)

def scrape_global_trending(driver):
    """Scrape topic cards from global trending (e.g. News, Sports)."""
    # Topic cards: div with grid classes containing a label (e.g. <p>News</p>)
    cards = driver.find_elements(
        By.CSS_SELECTOR,
        "div.jf-element.grid.grid-cols-1.grid-rows-1.w-full.h-full"
    )
    topics = []
    for card in cards:
        try:
            label = card.find_element(By.CSS_SELECTOR, "p.jf-element")
            name = label.text.strip()
            if name:
                topics.append((name, card))
                print(f"Trending topic: {name}")
        except Exception:
            pass
    return topics

def click_news_topic(driver):
    """Click the News topic button (by XPath or button containing news image)."""
    news_xpath = "/html/body/div[1]/div/div/div[2]/main/div/div/div/div/div/div/div[1]/div[1]/div[2]/div[2]/div/div/div/div/div[2]/div[1]/button"
    try:
        news_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, news_xpath))
        )
        driver.execute_script("arguments[0].click();", news_btn)
    except Exception:
        # Fallback: button that contains the News image
        news_img = driver.find_element(By.CSS_SELECTOR, 'img[src*="news_image_v7.jpg"]')
        news_btn = news_img.find_element(By.XPATH, "./ancestor::button")
        driver.execute_script("arguments[0].click();", news_btn)
    time.sleep(2)
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"]'))
    )
    time.sleep(4)  # let first batch of posts load fully

def _get_scrollable_feed(driver):
    """Find the scrollable element that contains the tweet feed (main column)."""
    # Strategy 1: scrollable ancestor of a tweet (overflow auto/scroll/overlay)
    tweets = driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
    if tweets:
        scrollable = driver.execute_script("""
            var el = arguments[0];
            while (el && el !== document.body) {
                var sh = el.scrollHeight, ch = el.clientHeight;
                if (sh > ch) {
                    var s = window.getComputedStyle(el);
                    var oy = s.overflowY || s.overflow;
                    if (oy === 'auto' || oy === 'scroll' || oy === 'overlay' || el === document.scrollingElement)
                        return el;
                }
                el = el.parentElement;
            }
            return document.scrollingElement || document.documentElement;
        """, tweets[0])
        if scrollable:
            return scrollable
    # Strategy 2: main element (X often puts feed in main)
    try:
        main = driver.find_element(By.TAG_NAME, "main")
        if main and driver.execute_script(
            "return arguments[0].scrollHeight > arguments[0].clientHeight;", main
        ):
            return main
    except Exception:
        pass
    return None

def _scroll_feed(driver, scrollable, amount=None):
    """Scroll the feed by amount (pixels) or to bottom if amount is None."""
    if scrollable:
        if amount is not None:
            driver.execute_script(
                "arguments[0].scrollTop += arguments[1];",
                scrollable, amount
            )
        else:
            driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight;",
                scrollable
            )
    else:
        if amount is not None:
            driver.execute_script("window.scrollBy(0, arguments[0]);", amount)
        else:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

def _get_tweet_author(tweet):
    """Extract @handle from tweet's User-Name section (link href or text)."""
    try:
        user_name_div = tweet.find_elements(By.CSS_SELECTOR, 'div[data-testid="User-Name"]')
        if not user_name_div:
            return ""
        # Profile link href is like /username or https://x.com/username
        links = user_name_div[0].find_elements(By.CSS_SELECTOR, 'a[href*="/"]')
        for a in links:
            href = a.get_attribute("href") or ""
            if "/status/" in href:
                continue
            # Extract handle: ...x.com/Handle or .../Handle
            parts = href.rstrip("/").split("/")
            if parts:
                handle = parts[-1]
                if handle and handle.lower() not in ("com", "x", "i", "intent"):
                    return "@" + handle
        # Fallback: User-Name text often "Display Name\n@handle"
        full = user_name_div[0].text or ""
        for part in full.split():
            if part.startswith("@"):
                return part
    except Exception:
        pass
    return ""

def _get_tweet_engagement(tweet, kind="like"):
    """Get like count or retweet count from tweet. Returns string (e.g. '1.2K' or '324')."""
    try:
        # X uses aria-label like "1,234 likes" or "12.5K likes"
        sel = f'[data-testid="{kind}"]' if kind in ("like", "retweet", "reply") else f'[aria-label*="{kind}"]'
        el = tweet.find_elements(By.CSS_SELECTOR, sel)
        if el:
            aria = el[0].get_attribute("aria-label") or ""
            # "1,234 likes" or "12.5K likes"
            m = re.search(r"([\d,\.]+[KMB]?)\s*(?:likes|Likes|retweets|Retweets|replies|Replies)", aria, re.I)
            if m:
                return m.group(1).strip()
            parent_text = el[0].find_element(By.XPATH, "./..").text or ""
            if parent_text:
                return parent_text.strip().split()[0] if parent_text.split() else ""
        # Fallback: aria-label on any engagement button
        for label in ("likes", "Likes", "retweets", "Retweets"):
            try:
                btn = tweet.find_element(By.CSS_SELECTOR, f'[aria-label*="{label}"]')
                aria = btn.get_attribute("aria-label") or ""
                m = re.search(r"([\d,\.]+[KMB]?)", aria)
                if m:
                    return m.group(1)
            except Exception:
                pass
    except Exception:
        pass
    return ""

def _get_tweet_impressions(tweet):
    """Impressions are rarely in the feed DOM; return if found."""
    try:
        # Some views show "X views" or "Impressions"
        for sel in ['[aria-label*="impression"]', '[aria-label*="view"]', '[data-testid*="view"]']:
            el = tweet.find_elements(By.CSS_SELECTOR, sel)
            if el:
                aria = el[0].get_attribute("aria-label") or ""
                m = re.search(r"([\d,\.]+[KMB]?)", aria)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""

def scrape_visible_tweets(driver, seen_texts):
    """
    Scrape all currently visible tweets. Returns list of dicts with keys:
    account, text, likes, retweets, impressions.
    Only returns tweets not in seen_texts; updates seen_texts.
    """
    new_tweets = []
    try:
        tweets = driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
        for tweet in tweets:
            try:
                text_el = tweet.find_elements(By.CSS_SELECTOR, 'div[data-testid="tweetText"]')
                text = (text_el[0].text if text_el else "").strip()
                if not text or text in seen_texts:
                    continue
                seen_texts.add(text)
                author = _get_tweet_author(tweet)
                likes = _get_tweet_engagement(tweet, "like")
                retweets = _get_tweet_engagement(tweet, "retweet")
                impressions = _get_tweet_impressions(tweet)
                new_tweets.append({
                    "account": author,
                    "text": text,
                    "likes": likes,
                    "retweets": retweets,
                    "impressions": impressions or "",  # often empty on feed
                })
            except Exception:
                pass
    except Exception:
        pass
    return new_tweets

def save_tweets_to_excel(rows, filepath=None):
    """Save list of tweet dicts to an Excel file."""
    if not rows:
        return
    if pd is None:
        print("Install pandas and openpyxl to save to Excel: pip install pandas openpyxl")
        return
    filepath = filepath or f"x_tweets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    df = pd.DataFrame(rows)
    col_order = ["account", "text", "likes", "retweets", "impressions"]
    df = df[[c for c in col_order if c in df.columns]]
    rename = {
        "account": "Account (ID / handle)",
        "text": "Tweet text",
        "likes": "Likes",
        "retweets": "Retweets",
        "impressions": "Impressions",
    }
    df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)
    df.to_excel(filepath, index=False, engine="openpyxl")
    print(f"Saved {len(rows)} tweets to {filepath}")

def scrape_and_scroll_loop(driver, rows_list):
    """
    Loop forever: scrape visible tweets (print new ones, append to rows_list), scroll,
    wait for new posts to render, repeat. Stops when the browser window is closed.
    """
    scrollable = _get_scrollable_feed(driver)
    viewport = 700
    if scrollable:
        try:
            viewport = driver.execute_script(
                "return arguments[0] ? arguments[0].clientHeight : window.innerHeight;",
                scrollable
            ) or 700
        except Exception:
            pass
    step = int(viewport * 0.7)
    if step <= 0:
        step = 500
    seen = set()
    count = 0
    print("Scraping and scrolling until you close the browser window. Data will be saved to Excel when you close the window.\n")
    while True:
        new_tweets = scrape_visible_tweets(driver, seen)
        for row in new_tweets:
            count += 1
            rows_list.append(row)
            print(f"--- Tweet {count} ---")
            if row.get("account"):
                print(f"Account: {row['account']}")
            print(row.get("text", ""))
            if row.get("likes"):
                print(f"Likes: {row['likes']}")
            if row.get("retweets"):
                print(f"Retweets: {row['retweets']}")
            print()
        _scroll_feed(driver, scrollable, amount=step)
        time.sleep(2.5)  # wait for new posts to render

def main():
    driver = setup_chrome()
    rows_list = []
    try:
        print("Loading X.com global trending...")
        load_x_trending(driver)

        print("\nScraping global trending topics:")
        scrape_global_trending(driver)

        print("\nClicking 'News' topic...")
        click_news_topic(driver)

        print("Waiting 90 seconds for the page to fully load before scraping...")
        time.sleep(90)
        print("\nScraping tweets in News (scrape → scroll → repeat until you close the window):\n")
        scrape_and_scroll_loop(driver, rows_list)
    except Exception as e:
        print(f"Stopped: {e}")
    finally:
        save_tweets_to_excel(rows_list)
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
