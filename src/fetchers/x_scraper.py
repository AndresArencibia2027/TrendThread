import time
import os
import re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    import pandas as pd
except ImportError:
    pd = None

# --- Configuration & Setup ---

def setup_chrome():
    """Initializes the Chrome driver with specific options to bypass detection."""
    chrome_options = Options()
    # High-quality User Agent to match modern browsers
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    return webdriver.Chrome(options=chrome_options)

# --- Internal Helper Functions ---

def _get_tweet_link(tweet):
    """Extracts the direct URL to the tweet post."""
    try:
        # The timestamp element is wrapped in a link to the status
        link_el = tweet.find_element(By.CSS_SELECTOR, 'time').find_element(By.XPATH, "./..")
        return link_el.get_attribute("href")
    except:
        return ""

def _get_tweet_media_links(tweet):
    """Extracts the source URLs for images and video thumbnails."""
    links = []
    try:
        # 1. Capture Image URLs
        # Images are found within the 'tweetPhoto' data-testid
        imgs = tweet.find_elements(By.CSS_SELECTOR, 'div[data-testid="tweetPhoto"] img')
        for img in imgs:
            src = img.get_attribute("src")
            if src:
                links.append(src)
        
        # 2. Capture Video/GIF Poster URLs
        # Video files themselves are often blobs, but we can capture the 'poster' (thumbnail)
        vids = tweet.find_elements(By.CSS_SELECTOR, 'div[data-testid="videoPlayer"] video')
        for vid in vids:
            poster = vid.get_attribute("poster")
            if poster:
                links.append(f"Poster: {poster}")
    except:
        pass
    
    return " | ".join(links) if links else "text-only"

def _get_scrollable_feed(driver):
    """Finds the scrollable element containing the tweet feed."""
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
        return scrollable
    return None

def _scroll_feed(driver, scrollable, amount=None):
    """Scrolls the feed by a specific pixel amount."""
    if scrollable:
        if amount is not None:
            driver.execute_script("arguments[0].scrollTop += arguments[1];", scrollable, amount)
        else:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scrollable)
    else:
        if amount is not None:
            driver.execute_script("window.scrollBy(0, arguments[0]);", amount)
        else:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

def _get_tweet_author(tweet):
    """Extracts the @handle from the tweet's User-Name section."""
    try:
        user_name_div = tweet.find_elements(By.CSS_SELECTOR, 'div[data-testid="User-Name"]')
        if not user_name_div: return ""
        links = user_name_div[0].find_elements(By.CSS_SELECTOR, 'a[href*="/"]')
        for a in links:
            href = a.get_attribute("href") or ""
            if "/status/" in href: continue
            parts = href.rstrip("/").split("/")
            if parts:
                handle = parts[-1]
                if handle and handle.lower() not in ("com", "x", "i", "intent"):
                    return "@" + handle
    except Exception:
        pass
    return ""

def _get_tweet_engagement(tweet, kind="like"):
    """Extracts engagement metrics (likes or retweets)."""
    try:
        sel = f'[data-testid="{kind}"]' if kind in ("like", "retweet", "reply") else f'[aria-label*="{kind}"]'
        el = tweet.find_elements(By.CSS_SELECTOR, sel)
        if el:
            aria = el[0].get_attribute("aria-label") or ""
            m = re.search(r"([\d,\.]+[KMB]?)", aria)
            return m.group(1) if m else ""
    except Exception:
        pass
    return ""

def scrape_visible_tweets(driver, seen_texts):
    """Scrapes currently visible tweets and returns a list of dictionaries."""
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
                new_tweets.append({
                    "account": _get_tweet_author(tweet),
                    "tweet_url": _get_tweet_link(tweet), # NEW: Link to post
                    "text": text,
                    "media_links": _get_tweet_media_links(tweet), # NEW: Links to media files
                    "likes": _get_tweet_engagement(tweet, "like"),
                    "retweets": _get_tweet_engagement(tweet, "retweet")
                })
            except Exception:
                continue
    except Exception:
        pass
    return new_tweets

# --- Main Orchestrator Function ---

def run_full_x_scraper(out_dir="data/raw", max_tweets=50):
    """
    Orchestrates the X scraper. 
    Stops when max_tweets are captured OR the browser window is closed.
    """
    driver = setup_chrome()
    rows_list = []
    os.makedirs(out_dir, exist_ok=True)
    
    filename = f"x_tweets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = os.path.join(out_dir, filename)

    try:
        print(f" Loading X.com... Target Goal: {max_tweets} tweets.")
        driver.get("https://x.com/i/jf/global-trending/home")
        
        # Wait for the trending interface to load
        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.jf-element.grid"))
        )
        time.sleep(2)

        # Click 'News' topic using your original working XPath
        print(" Clicking 'News' topic...")
        news_xpath = "/html/body/div[1]/div/div/div[2]/main/div/div/div/div/div/div/div[1]/div[1]/div[2]/div[2]/div/div/div/div/div[2]/div[1]/button"
        news_btn = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, news_xpath)))
        driver.execute_script("arguments[0].click();", news_btn)
        
        print(f" Waiting for feed to load...")
        time.sleep(10) # Your preferred 10s wait

        seen = set()
        scrollable = _get_scrollable_feed(driver)
        print(f" Starting scrape loop. Goal: {max_tweets} tweets.")

        while len(rows_list) < max_tweets:
            try:
                # Check if window is still open
                _ = driver.window_handles 
                
                new_data = scrape_visible_tweets(driver, seen)
                for item in new_data:
                    if len(rows_list) < max_tweets:
                        rows_list.append(item)
                        print(f"[{len(rows_list)}/{max_tweets}]  Captured: {item['text'][:50]}...")
                
                if len(rows_list) >= max_tweets:
                    print(" Target reached!")
                    break

                _scroll_feed(driver, scrollable, amount=700)
                time.sleep(3)
                
            except Exception:
                # Triggers if the browser is closed manually
                print("\n Browser closed. Finalizing data...")
                break

    finally:
        if rows_list and pd:
            df = pd.DataFrame(rows_list)
            # Reorder columns for clarity
            cols = ["account", "tweet_url", "text", "media_links", "likes", "retweets"]
            df = df[cols]
            df.to_excel(filepath, index=False, engine="openpyxl")
            print(f" Success! Saved {len(rows_list)} tweets with media links to {filepath}")
        else:
            print(" No data was captured.")
            
        try:
            driver.quit()
        except:
            pass
            
    return filepath