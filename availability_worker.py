"""
Playwright を別プロセスで実行するワーカースクリプト。
JSONファイルパスを引数で受け取り、結果をJSONファイルに書き出す。
認証情報は環境変数から読み込む。
"""

import asyncio
import json
import os
import sys
import re
from datetime import date, timedelta
from playwright.async_api import async_playwright

def get_credentials():
    """環境変数から認証情報を取得する。"""
    return {
        "場所とる": {
            "url": "https://bashotoru.com/",
            "id": os.environ.get("BASHOTORU_ID", ""),
            "pw": os.environ.get("BASHOTORU_PW", ""),
        },
        "スペースラボ": {
            "url": "https://spacelab-system.jp/",
            "id": os.environ.get("SPACELAB_ID", ""),
            "pw": os.environ.get("SPACELAB_PW", ""),
        },
        "楽市": {
            "url": "https://rakuichi-space.com/login",
            "id": os.environ.get("RAKUICHI_ID", ""),
            "pw": os.environ.get("RAKUICHI_PW", ""),
        },
        "自由市場": {
            "url": "https://www.jiyu18.jp/mypage/members/login",
            "id": os.environ.get("JIYU_ID", ""),
            "pw": os.environ.get("JIYU_PW", ""),
        },
        "ダイエースペース": {
            "url": "https://web-space.daiei-spacecreate.com/",
            "id": os.environ.get("DAIEI_ID", ""),
            "pw": os.environ.get("DAIEI_PW", ""),
        },
    }

SITE_KEYWORD_MAP = {
    "場所とる": ["場所とる", "場所取る"],
    "スペースラボ": ["スペースラボ"],
    "楽市": ["楽市"],
    "自由市場": ["自由市場"],
    "ダイエースペース": ["ダイエースペース"],
}

MANUAL_KEYWORDS = ["グラスト", "グラスと", "アイフィールド", "ライフ", "ネクサスイノベーション"]


def classify_vendor(vendor):
    if not vendor or str(vendor).strip() in ["", "nan"]:
        return [], False
    parts = [p.strip() for p in re.split(r"[／/]", str(vendor)) if p.strip()]
    supported = []
    has_unsupported = False
    for part in parts:
        matched = False
        for site_name, keywords in SITE_KEYWORD_MAP.items():
            if any(kw in part for kw in keywords):
                if site_name not in supported:
                    supported.append(site_name)
                matched = True
                break
        if not matched:
            for kw in MANUAL_KEYWORDS:
                if kw in part:
                    has_unsupported = True
                    break
    return supported, has_unsupported


async def scan_calendar(page, start_date_str, end_date_str):
    date_texts = set()
    current = date.fromisoformat(start_date_str)
    end = date.fromisoformat(end_date_str)
    while current <= end:
        date_texts.add(current.strftime("%Y-%m-%d"))
        date_texts.add(f"{current.month}/{current.day}")
        date_texts.add(f"{current.month:02d}/{current.day:02d}")
        current += timedelta(days=1)

    content = await page.content()
    booked_selectors = [
        ".booked", ".reserved", ".full", ".unavailable",
        "[class*='booked']", "[class*='reserved']", "[class*='full']",
        "td.booked", "td.reserved", "td.full",
    ]
    for sel in booked_selectors:
        try:
            elements = page.locator(sel)
            count = await elements.count()
            for i in range(count):
                elem = elements.nth(i)
                text = (await elem.text_content() or "").strip()
                aria = await elem.get_attribute("aria-label") or ""
                data_date = await elem.get_attribute("data-date") or ""
                for d_str in date_texts:
                    if d_str and (d_str in text or d_str in aria or d_str in data_date):
                        return "予約済みあり"
        except Exception:
            pass

    for word in ["×", "満", "予約不可", "受付不可", "FULL"]:
        if word in content:
            return "予約済みあり（要詳細確認）"
    return "空きあり"


async def check_site(page, site_name, facility_name, venue_name, start_date_str, end_date_str):
    try:
        credentials = get_credentials()
        creds = credentials[site_name]
        await page.goto(creds["url"], timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=60000)

        if site_name == "ダイエースペース":
            id_sel = 'input[name="id"], input[name="user_id"], input[name="login_id"], input[type="text"]:first-of-type'
        else:
            id_sel = 'input[type="email"], input[name="email"], input[name="username"], input[name="login_id"]'

        await page.wait_for_selector(id_sel, timeout=30000)
        await page.fill(id_sel, creds["id"])
        await page.fill('input[type="password"]', creds["pw"])
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("networkidle", timeout=60000)

        search_sel = 'input[placeholder*="検索"], input[type="search"], input[name*="keyword"], input[placeholder*="施設名"]'
        if await page.locator(search_sel).count() > 0:
            await page.locator(search_sel).first.fill(venue_name)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle", timeout=45000)

        link = page.locator(f'a:has-text("{venue_name}")').first
        if await link.count() == 0:
            link = page.locator(f'a:has-text("{facility_name}")').first
        if await link.count() == 0:
            return "会場が見つかりません"
        await link.click()
        await page.wait_for_load_state("networkidle", timeout=45000)
        return await scan_calendar(page, start_date_str, end_date_str)
    except Exception as e:
        return f"確認エラー: {str(e)[:80]}"


async def main():
    input_path = sys.argv[1]
    output_path = sys.argv[2]

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    venues = data["venues"]
    start_date = data["start_date"]
    end_date = data["end_date"]

    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            for venue in venues:
                vendor = str(venue.get("発注先", "") or "")
                facility_name = str(venue.get("施設名", "") or "")
                venue_name = str(venue.get("実施場所名", "") or "")
                supported_sites, has_unsupported = classify_vendor(vendor)

                venue_copy = dict(venue)

                if not supported_sites:
                    venue_copy["確認サイト"] = vendor if vendor else "不明"
                    venue_copy["確認結果"] = "手動確認要"
                    venue_copy["要手動確認"] = True
                    venue_copy["_excluded"] = False
                    results.append(venue_copy)
                    continue

                site_results = []
                for site_name in supported_sites:
                    context = await browser.new_context()
                    page = await context.new_page()
                    try:
                        r = await check_site(page, site_name, facility_name, venue_name, start_date, end_date)
                        site_results.append(f"{site_name}:{r}")
                    finally:
                        await context.close()

                venue_copy["確認サイト"] = "、".join(supported_sites)
                if has_unsupported:
                    venue_copy["確認サイト"] += "（＋手動確認要）"
                venue_copy["確認結果"] = "、".join(site_results)
                venue_copy["要手動確認"] = has_unsupported
                venue_copy["_excluded"] = any("予約済み" in r for r in site_results)
                results.append(venue_copy)
        finally:
            await browser.close()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
