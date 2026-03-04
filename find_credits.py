#!/usr/bin/env python3
"""
Reverse image search each production photo using Google Cloud Vision API
and scrape matching pages for photographer credits / captions.

Requirements:
    pip install google-cloud-vision requests beautifulsoup4

Usage:
    export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your-service-account.json"
    python find_credits.py

    OR pass API key directly:
    python find_credits.py --api-key YOUR_KEY
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

IMAGES_DIR = Path(__file__).parent / "images" / "Production Shots"
OUTPUT_FILE = Path(__file__).parent / "credits_research.txt"

# Keywords that suggest a photographer credit nearby
CREDIT_PATTERNS = [
    re.compile(r"photo(?:graph)?(?:s|y)?\s*(?:by|:)\s*([A-Z][a-zA-Z\s\-]+)", re.IGNORECASE),
    re.compile(r"(?:image|pic(?:ture)?)\s*(?:by|credit|:)\s*([A-Z][a-zA-Z\s\-]+)", re.IGNORECASE),
    re.compile(r"©\s*([A-Z][a-zA-Z\s\-]+)", re.IGNORECASE),
    re.compile(r"copyright\s*([A-Z][a-zA-Z\s\-]+)", re.IGNORECASE),
    re.compile(r"\bcredit[s]?\s*(?:to|:)?\s*([A-Z][a-zA-Z\s\-]+)", re.IGNORECASE),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def vision_web_detect(image_path: Path, api_key: str) -> dict:
    """Call Vision API WEB_DETECTION and return raw response dict."""
    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
    payload = {
        "requests": [
            {
                "image": {"content": encode_image(image_path)},
                "features": [{"type": "WEB_DETECTION", "maxResults": 10}],
            }
        ]
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["responses"][0].get("webDetection", {})


def scrape_page(url: str) -> dict:
    """Fetch a URL and return title, meta description, and any credit snippets."""
    result = {"url": url, "title": "", "description": "", "credits": [], "error": None}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        result["title"] = soup.title.string.strip() if soup.title else ""

        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            result["description"] = meta_desc.get("content", "").strip()

        # Search visible text for credit patterns
        text = soup.get_text(separator=" ", strip=True)
        for pattern in CREDIT_PATTERNS:
            for match in pattern.finditer(text):
                credit = match.group(0).strip()
                if credit and credit not in result["credits"]:
                    result["credits"].append(credit)

        # Also look for alt text on images that might contain the filename
        for img in soup.find_all("img"):
            alt = img.get("alt", "").strip()
            if alt and len(alt) > 4:
                for pattern in CREDIT_PATTERNS:
                    for match in pattern.finditer(alt):
                        credit = match.group(0).strip()
                        if credit not in result["credits"]:
                            result["credits"].append(credit)

    except Exception as e:
        result["error"] = str(e)

    return result


def process_image(image_path: Path, api_key: str) -> dict:
    print(f"  [Vision API] {image_path.name}")
    detection = vision_web_detect(image_path, api_key)

    pages = detection.get("pagesWithMatchingImages", [])
    entities = [e.get("description", "") for e in detection.get("webEntities", []) if e.get("score", 0) > 0.5]
    full_matches = [m.get("url", "") for m in detection.get("fullMatchingImages", [])]
    partial_matches = [m.get("url", "") for m in detection.get("partialMatchingImages", [])]

    scraped = []
    for page in pages[:4]:  # limit to top 4 pages per image
        page_url = page.get("url", "")
        if not page_url:
            continue
        print(f"    Scraping: {page_url[:80]}")
        scraped.append(scrape_page(page_url))
        time.sleep(0.5)  # be polite

    return {
        "file": image_path.name,
        "entities": entities,
        "full_match_urls": full_matches,
        "partial_match_urls": partial_matches,
        "pages": scraped,
    }


def format_result(result: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append(f"FILE: {result['file']}")
    lines.append("=" * 70)

    if result["entities"]:
        lines.append(f"Vision labels: {', '.join(result['entities'])}")

    if result["full_match_urls"]:
        lines.append("\nFull image matches:")
        for url in result["full_match_urls"][:5]:
            lines.append(f"  {url}")

    if not result["pages"]:
        lines.append("\nNo matching web pages found.")
    else:
        lines.append(f"\nMatching pages ({len(result['pages'])} scraped):")
        for page in result["pages"]:
            lines.append(f"\n  URL:   {page['url']}")
            if page.get("error"):
                lines.append(f"  ERROR: {page['error']}")
                continue
            if page.get("title"):
                lines.append(f"  Title: {page['title']}")
            if page.get("description"):
                lines.append(f"  Desc:  {page['description'][:200]}")
            if page.get("credits"):
                lines.append("  Credit snippets found:")
                for c in page["credits"][:5]:
                    lines.append(f"    - {c}")
            else:
                lines.append("  (no credit patterns found on page)")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Reverse image search production photos")
    parser.add_argument("--api-key", help="Google Cloud Vision API key")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GOOGLE_CLOUD_API_KEY")
    if not api_key:
        print("Error: provide --api-key or set GOOGLE_CLOUD_API_KEY env var")
        sys.exit(1)

    image_files = sorted(
        [f for f in IMAGES_DIR.iterdir() if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    )
    print(f"Found {len(image_files)} images in '{IMAGES_DIR}'")

    all_results = []
    for i, img in enumerate(image_files, 1):
        print(f"\n[{i}/{len(image_files)}] {img.name}")
        try:
            result = process_image(img, api_key)
        except Exception as e:
            result = {"file": img.name, "entities": [], "full_match_urls": [], "partial_match_urls": [], "pages": [], "error": str(e)}
            print(f"  ERROR: {e}")
        all_results.append(result)
        time.sleep(1)  # stay within rate limits

    output = "\n".join(format_result(r) for r in all_results)
    OUTPUT_FILE.write_text(output, encoding="utf-8")
    print(f"\nDone. Results written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
