"""
BSE India - Company Updates / Earnings Call Transcripts Scraper
Downloads all PDF announcements to a timestamped local folder.

Usage:
    python bse_scraper.py

Requirements:
    pip install -r requirements.txt
    ChromeDriver must match your installed Chrome version, OR
    webdriver-manager handles it automatically.
"""

import os
import time
import requests
from datetime import datetime
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup


# ── Configuration ─────────────────────────────────────────────────────────────

BSE_URL = "https://www.bseindia.com/corporates/ann.html"
CATEGORY_TEXT = "Company Update"          # exact text shown in the dropdown
SUBCATEGORY_TEXT = "Earnings Call Transcript"  # exact text shown in the dropdown
PAGE_LOAD_WAIT = 5                        # seconds to wait after selecting filters
REQUEST_TIMEOUT = 30                      # seconds per PDF download request

# Headers that mimic a real browser to avoid 403s on PDF endpoints
DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bseindia.com/",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def make_output_dir() -> str:
    """Create and return a timestamped output directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = f"Company_Updates_Earnings_Call_Transcript_{timestamp}"
    os.makedirs(folder, exist_ok=True)
    print(f"[+] Output folder: {folder}")
    return folder


def build_driver() -> webdriver.Chrome:
    """Return a headless Chrome WebDriver."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def select_dropdown_by_text(driver: webdriver.Chrome, element_id: str, visible_text: str):
    """Select a <select> option by its visible text (partial match fallback)."""
    wait = WebDriverWait(driver, 20)
    element = wait.until(EC.presence_of_element_located((By.ID, element_id)))
    sel = Select(element)

    # Try exact match first
    for option in sel.options:
        if option.text.strip() == visible_text:
            sel.select_by_visible_text(option.text.strip())
            print(f"    Selected '{option.text.strip()}' from #{element_id}")
            return

    # Fallback: partial / case-insensitive match
    for option in sel.options:
        if visible_text.lower() in option.text.strip().lower():
            sel.select_by_visible_text(option.text.strip())
            print(f"    Selected (partial match) '{option.text.strip()}' from #{element_id}")
            return

    available = [o.text.strip() for o in sel.options]
    raise ValueError(
        f"Could not find '{visible_text}' in #{element_id}.\n"
        f"Available options: {available}"
    )


def fetch_pdf_links(driver: webdriver.Chrome) -> list[dict]:
    """
    Parse the results table with BeautifulSoup and return a list of dicts:
        [{"company": str, "date": str, "subject": str, "pdf_url": str}, ...]
    """
    soup = BeautifulSoup(driver.page_source, "html.parser")

    records = []

    # BSE renders announcements inside a div/table; look for anchor tags
    # whose href points to a PDF on bseindia.com
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()

        # Only keep links that look like PDFs
        if not (href.lower().endswith(".pdf") or "annxd" in href.lower() or "pdf" in href.lower()):
            continue

        # Resolve relative URLs
        if href.startswith("http"):
            pdf_url = href
        else:
            pdf_url = urljoin("https://www.bseindia.com", href)

        # Walk up the DOM to grab surrounding row context (company name / date)
        row = anchor.find_parent("tr")
        cells = row.find_all("td") if row else []
        cell_texts = [c.get_text(strip=True) for c in cells]

        records.append({
            "company": cell_texts[0] if len(cell_texts) > 0 else "Unknown",
            "date":    cell_texts[1] if len(cell_texts) > 1 else "Unknown",
            "subject": cell_texts[2] if len(cell_texts) > 2 else anchor.get_text(strip=True),
            "pdf_url": pdf_url,
        })

    # De-duplicate by URL
    seen = set()
    unique = []
    for r in records:
        if r["pdf_url"] not in seen:
            seen.add(r["pdf_url"])
            unique.append(r)

    return unique


def download_pdf(session: requests.Session, pdf_url: str, dest_path: str) -> bool:
    """Download a single PDF; returns True on success."""
    try:
        response = session.get(
            pdf_url,
            headers=DOWNLOAD_HEADERS,
            timeout=REQUEST_TIMEOUT,
            stream=True,
        )
        response.raise_for_status()

        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        size_kb = os.path.getsize(dest_path) / 1024
        print(f"    Saved  ({size_kb:.1f} KB)  →  {os.path.basename(dest_path)}")
        return True

    except requests.RequestException as exc:
        print(f"    [!] Failed to download {pdf_url}: {exc}")
        return False


def safe_filename(text: str, max_len: int = 80) -> str:
    """Convert arbitrary text into a safe filename fragment."""
    keep = " abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
    name = "".join(c if c in keep else "_" for c in text)
    name = "_".join(name.split())          # collapse whitespace
    return name[:max_len]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    output_dir = make_output_dir()
    driver = build_driver()

    try:
        print(f"\n[1] Opening {BSE_URL} …")
        driver.get(BSE_URL)

        # Give the page an initial moment to render JS
        time.sleep(3)

        # ── Select Category ──────────────────────────────────────────────────
        print("[2] Selecting Category …")
        # Common element IDs on BSE ann page (inspect the live page if these change)
        # Try several candidate IDs used on different versions of the page
        category_ids = ["ddlPeriod", "ddlcategory", "Category", "ddlCategory"]
        category_selected = False
        for cid in category_ids:
            try:
                select_dropdown_by_text(driver, cid, CATEGORY_TEXT)
                category_selected = True
                break
            except Exception:
                continue

        if not category_selected:
            # Last resort: find by XPath label proximity
            selects = driver.find_elements(By.TAG_NAME, "select")
            print(f"    [!] Could not find category dropdown by ID. Found {len(selects)} <select> elements.")
            print("    Available selects and their options:")
            for s in selects:
                sel_obj = Select(s)
                opts = [o.text.strip() for o in sel_obj.options]
                print(f"      id='{s.get_attribute('id')}' name='{s.get_attribute('name')}' → {opts}")
            raise RuntimeError(
                "Could not locate the Category dropdown. "
                "Inspect the page and update 'category_ids' in the script."
            )

        time.sleep(1)  # allow sub-category dropdown to populate

        # ── Select Sub-Category ──────────────────────────────────────────────
        print("[3] Selecting Sub-Category …")
        subcategory_ids = ["ddlsubcategory", "SubCategory", "ddlSubCategory", "ddlSubPeriod"]
        subcat_selected = False
        for sid in subcategory_ids:
            try:
                select_dropdown_by_text(driver, sid, SUBCATEGORY_TEXT)
                subcat_selected = True
                break
            except Exception:
                continue

        if not subcat_selected:
            selects = driver.find_elements(By.TAG_NAME, "select")
            print(f"    [!] Could not find sub-category dropdown by ID.")
            for s in selects:
                sel_obj = Select(s)
                opts = [o.text.strip() for o in sel_obj.options]
                print(f"      id='{s.get_attribute('id')}' → {opts}")
            raise RuntimeError(
                "Could not locate the Sub-Category dropdown. "
                "Inspect the page and update 'subcategory_ids' in the script."
            )

        # ── Submit / trigger search if there's a button ──────────────────────
        try:
            submit_btn = driver.find_element(By.ID, "btnSubmit")
            submit_btn.click()
            print("[4] Clicked submit button.")
        except Exception:
            # Some BSE pages auto-reload on dropdown change — no button needed
            print("[4] No submit button found; results should load automatically.")

        # ── Wait for results ─────────────────────────────────────────────────
        print(f"[5] Waiting {PAGE_LOAD_WAIT}s for results to load …")
        time.sleep(PAGE_LOAD_WAIT)

        # ── Parse results ────────────────────────────────────────────────────
        print("[6] Parsing page for PDF links …")
        records = fetch_pdf_links(driver)

        if not records:
            print(
                "\n[!] No PDF links found.\n"
                "    This may mean:\n"
                "      • The dropdown IDs have changed — open the page in a browser,\n"
                "        inspect the elements, and update the ID lists in this script.\n"
                "      • The page requires additional interaction (CAPTCHA, login).\n"
                "      • The result table uses a different HTML structure.\n"
                "    The raw page source has been saved to 'debug_page.html' for inspection."
            )
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            return

        print(f"    Found {len(records)} PDF link(s).")

        # ── Download PDFs ────────────────────────────────────────────────────
        print("[7] Downloading PDFs …")
        session = requests.Session()
        session.headers.update(DOWNLOAD_HEADERS)

        success_count = 0
        for idx, rec in enumerate(records, start=1):
            company_safe = safe_filename(rec["company"])
            date_safe = safe_filename(rec["date"]).replace(" ", "_")
            filename = f"{idx:03d}_{company_safe}_{date_safe}.pdf"
            dest = os.path.join(output_dir, filename)

            print(f"  [{idx}/{len(records)}] {rec['company']} | {rec['date']}")
            print(f"    URL: {rec['pdf_url']}")

            if download_pdf(session, rec["pdf_url"], dest):
                success_count += 1

        print(
            f"\n[Done] Downloaded {success_count}/{len(records)} PDFs "
            f"→ '{output_dir}'"
        )

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
