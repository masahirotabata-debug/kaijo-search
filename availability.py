"""
各予約サイトへの空き確認処理。
Playwright (chromium, headless) を使用して各サイトにログインし、
指定期間の空き状況を確認する。
Windows + Streamlit 対応版（asyncio.run → nest_asyncio 使用）
"""

import asyncio
import re
from datetime import date, timedelta

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# ───────────────────────────────────────────────
# 認証情報（ハードコード）
# ───────────────────────────────────────────────
CREDENTIALS: dict[str, dict] = {
    "場所とる": {
        "url": "https://bashotoru.com/",
        "id": "ba1@agent-network.com",
        "pw": "Agent0416T",
    },
    "スペースラボ": {
        "url": "https://spacelab-system.jp/",
        "id": "shun.sato@agent-network.com",
        "pw": "Agent0401",
    },
    "楽市": {
        "url": "https://rakuichi-space.com/login",
        "id": "shun.sato@agent-network.com",
        "pw": "Fz8sWeuB",
    },
    "自由市場": {
        "url": "https://www.jiyu18.jp/mypage/members/login",
        "id": "ba2@agent-network.com",
        "pw": "agent0416",
    },
    "ダイエースペース": {
        "url": "https://web-space.daiei-spacecreate.com/",
        "id": "300118",
        "pw": "Passw0rd",
    },
}

UNSUPPORTED_KEYWORDS = ["グラスト", "グラスと", "アイフィールド", "ライフ", "ネクサスイノベーション"]

SITE_KEYWORD_MAP: dict[str, list[str]] = {
    "場所とる": ["場所とる", "場所取る"],
    "スペースラボ": ["スペースラボ"],
    "楽市": ["楽市"],
    "自由市場": ["自由市場"],
    "ダイエースペース": ["ダイエースペース"],
}


# ───────────────────────────────────────────────
# 発注先の解析
# ───────────────────────────────────────────────
def classify_vendor(vendor: str) -> tuple[list[str], bool]:
    if not vendor or str(vendor).strip() in ["", "nan"]:
        return [], False

    raw_parts = re.split(r"[／/]", str(vendor))
    parts = [p.strip() for p in raw_parts if p.strip()]

    supported: list[str] = []
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
            for kw in UNSUPPORTED_KEYWORDS:
                if kw in part:
                    has_unsupported = True
                    break

    return supported, has_unsupported


# ───────────────────────────────────────────────
# カレンダースキャン（汎用）
# ───────────────────────────────────────────────
async def _scan_calendar(page: Page, start_date: date, end_date: date) -> str:
    booked_selectors = [
        ".booked", ".reserved", ".full", ".unavailable",
        ".fc-daygrid-day.booked", ".calendar-reserved",
        "[class*='booked']", "[class*='reserved']", "[class*='full']",
        "td.booked", "td.reserved", "td.full",
    ]

    date_texts: set[str] = set()
    current = start_date
    while current <= end_date:
        date_texts.add(current.strftime("%Y-%m-%d"))
        date_texts.add(f"{current.month}/{current.day}")
        date_texts.add(f"{current.month:02d}/{current.day:02d}")
        date_texts.add(str(current.day))
        current += timedelta(days=1)

    content = await page.content()

    for sel in booked_selectors:
        try:
            elements = page.locator(sel)
            count = await elements.count()
            if count > 0:
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

    booked_words = ["×", "満", "予約不可", "受付不可", "FULL", "✕"]
    for word in booked_words:
        if word in content:
            return "予約済みあり（要詳細確認）"

    return "空きあり"


# ───────────────────────────────────────────────
# サイト別空き確認ロジック
# ───────────────────────────────────────────────
async def _check_site(page: Page, site_name: str, facility_name: str,
                      venue_name: str, start_date: date, end_date: date) -> str:
    try:
        creds = CREDENTIALS[site_name]
        await page.goto(creds["url"], timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=30000)

        if site_name == "ダイエースペース":
            id_sel = 'input[name="id"], input[name="user_id"], input[name="login_id"], input[type="text"]:first-of-type'
        else:
            id_sel = 'input[type="email"], input[name="email"], input[name="username"], input[name="login_id"]'

        await page.wait_for_selector(id_sel, timeout=10000)
        await page.fill(id_sel, creds["id"])
        await page.fill('input[type="password"]', creds["pw"])
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("networkidle", timeout=30000)

        search_sel = 'input[type="search"], input[name*="keyword"], input[name*="search"], input[placeholder*="検索"]'
        search_inputs = page.locator(search_sel)
        if await search_inputs.count() > 0:
            await search_inputs.first.fill(venue_name)
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle", timeout=20000)

        link = page.locator(f'a:has-text("{venue_name}")').first
        if await link.count() == 0:
            link = page.locator(f'a:has-text("{facility_name}")').first
        if await link.count() == 0:
            return "会場が見つかりません"
        await link.click()
        await page.wait_for_load_state("networkidle", timeout=20000)

        return await _scan_calendar(page, start_date, end_date)

    except Exception as e:
        return f"確認エラー: {str(e)[:80]}"


async def _check_venue_async(browser: Browser, site_name: str, facility_name: str,
                              venue_name: str, start_date: date, end_date: date) -> tuple[str, str]:
    context: BrowserContext = await browser.new_context()
    page: Page = await context.new_page()
    try:
        result = await _check_site(page, site_name, facility_name, venue_name, start_date, end_date)
        return site_name, result
    finally:
        await context.close()


async def check_all_venues_async(venues: list[dict], start_date: date,
                                  end_date: date, progress_callback=None) -> list[dict]:
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            tasks = []
            venue_site_pairs = []

            for venue in venues:
                vendor = str(venue.get("発注先", "") or "")
                facility_name = str(venue.get("施設名", "") or "")
                venue_name = str(venue.get("実施場所名", "") or "")

                supported_sites, has_unsupported = classify_vendor(vendor)

                if not supported_sites:
                    venue_copy = dict(venue)
                    venue_copy["確認サイト"] = vendor if vendor else "不明"
                    venue_copy["確認結果"] = "確認不可（手動確認要）"
                    venue_copy["要手動確認"] = True
                    results.append(venue_copy)
                    continue

                for site_name in supported_sites:
                    task = asyncio.ensure_future(
                        _check_venue_async(browser, site_name, facility_name, venue_name, start_date, end_date)
                    )
                    tasks.append(task)
                    venue_site_pairs.append((venue, site_name, has_unsupported))

            if tasks:
                task_results = await asyncio.gather(*tasks, return_exceptions=True)
            else:
                task_results = []

            venue_results: dict[int, dict] = {}
            for i, (venue, site_name, has_unsupported) in enumerate(venue_site_pairs):
                vid = id(venue)
                if vid not in venue_results:
                    venue_results[vid] = {"venue": venue, "sites": [], "results": [], "has_unsupported": has_unsupported}
                result = task_results[i]
                site_result = f"確認エラー: {str(result)[:80]}" if isinstance(result, Exception) else result[1]
                venue_results[vid]["sites"].append(site_name)
                venue_results[vid]["results"].append(site_result)
                if progress_callback:
                    progress_callback(f"✅ {site_name}: {venue.get('実施場所名', '')} → {site_result}")

            for vid, vdata in venue_results.items():
                venue_copy = dict(vdata["venue"])
                site_str = "、".join(vdata["sites"])
                if vdata["has_unsupported"]:
                    site_str += "（＋手動確認要）"
                venue_copy["確認サイト"] = site_str
                all_results = vdata["results"]
                if any("予約済み" in r for r in all_results):
                    venue_copy["確認結果"] = "予約済みあり（除外）"
                    venue_copy["要手動確認"] = vdata["has_unsupported"]
                    venue_copy["_excluded"] = True
                elif any("エラー" in r or "見つかりません" in r for r in all_results):
                    venue_copy["確認結果"] = "、".join(all_results)
                    venue_copy["要手動確認"] = True
                    venue_copy["_excluded"] = False
                else:
                    venue_copy["確認結果"] = "、".join(all_results)
                    venue_copy["要手動確認"] = vdata["has_unsupported"]
                    venue_copy["_excluded"] = False
                results.append(venue_copy)

        finally:
            await browser.close()

    return results


def run_availability_check(venues: list[dict], start_date: date,
                            end_date: date, progress_callback=None) -> list[dict]:
    """同期ラッパー（Streamlit + Windows 対応版）。"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(
        check_all_venues_async(venues, start_date, end_date, progress_callback)
    )
