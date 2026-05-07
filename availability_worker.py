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
    return {
        "場所とる": {
            "url": "https://bashotoru.com/login",
            "id": os.environ.get("BASHOTORU_ID", ""),
            "pw": os.environ.get("BASHOTORU_PW", ""),
        },
        "スペースラボ": {
            "url": "https://spacelab-system.jp/login/",
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

MANUAL_KEYWORDS = ["グラスト", "グラスと", "アイフィールド", "ライト", "ネクサスイノーション"]


def classify_vendor(vendor):
    if not vendor or str(vendor).strip() in ["", "nan"]:
        return [], False
    parts = [p.strip() for p in re.split(r"[・／]", str(vendor)) if p.strip()]
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
                        return "予約済み（空きあり）"
        except Exception:
            pass

    for word in ["予約不可", "受付不可", "FULL", "空きなし", "満室", "貸出不可"]:
        if word in content:
            return "予約済み（詳細確認推奨）"
    return "空きあり"


async def login_daiei(page, creds):
    """ダイエースペース ログイン処理"""
    await page.goto(creds["url"], timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=60000)
    # クッキー同意ボタンがあれば閉じる
    try:
        await page.click('button:has-text("同意する")', timeout=5000)
        await page.wait_for_timeout(1000)
    except Exception:
        pass
    # ログインメニューをクリックしてフォームを表示（ナビの「ログイン」li要素）
    try:
        login_nav = page.locator('li').filter(has_text="ログイン").first
        await login_nav.click(timeout=10000)
        await page.wait_for_timeout(2000)
    except Exception:
        pass
    # IDとパスワードを入力
    try:
        # まずinput[type="text"]でID入力を試みる
        id_input = page.locator('input[type="text"]').first
        await id_input.wait_for(timeout=10000)
        await id_input.fill(creds["id"])
    except Exception:
        # なければinput全体の最初
        inputs = page.locator('input:not([type="hidden"])')
        if await inputs.count() > 0:
            await inputs.nth(0).fill(creds["id"])
    pw_inputs = page.locator('input[type="password"]')
    if await pw_inputs.count() > 0:
        await pw_inputs.first.fill(creds["pw"])
    # ログインボタンをクリック（「ログインする >」というテキスト）
    try:
        await page.click('a:has-text("ログインする"), button:has-text("ログインする"), input[type="submit"]', timeout=10000)
    except Exception:
        # フォームをEnterで送信
        await page.keyboard.press("Enter")
    await page.wait_for_load_state("networkidle", timeout=60000)


async def login_bashotoru(page, creds):
    """場所とる ログイン処理"""
    await page.goto(creds["url"], timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=60000)
    # 場所とるはSPA、inputを順番で取得
    inputs = page.locator('input')
    count = await inputs.count()
    if count >= 2:
        await inputs.nth(0).fill(creds["id"])
        await inputs.nth(1).fill(creds["pw"])
    await page.click('button[type="submit"], button:has-text("ログイン")')
    await page.wait_for_load_state("networkidle", timeout=60000)


async def login_spacelab(page, creds):
    """スペースラボ ログイン処理"""
    await page.goto(creds["url"], timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=60000)
    id_sel = 'input[name="login_id"], input[name="email"], input[name="id"], input[type="text"]'
    pw_sel = 'input[type="password"]'
    await page.wait_for_selector(id_sel, timeout=30000)
    await page.fill(id_sel, creds["id"])
    await page.fill(pw_sel, creds["pw"])
    await page.click('input[type="submit"], button[type="submit"], button:has-text("ログイン")')
    await page.wait_for_load_state("networkidle", timeout=60000)


async def login_generic(page, creds):
    """汎用ログイン処理"""
    await page.goto(creds["url"], timeout=60000)
    await page.wait_for_load_state("networkidle", timeout=60000)
    id_sel = 'input[type="email"], input[name="email"], input[name="username"], input[name="login_id"], input[name="id"], input[type="text"]'
    pw_sel = 'input[type="password"]'
    await page.wait_for_selector(id_sel, timeout=30000)
    await page.fill(id_sel, creds["id"])
    await page.fill(pw_sel, creds["pw"])
    await page.click('button[type="submit"], input[type="submit"]')
    await page.wait_for_load_state("networkidle", timeout=60000)


async def check_site(page, site_name, facility_name, venue_name, start_date_str, end_date_str):
    try:
        credentials = get_credentials()
        creds = credentials[site_name]

        # サイトごとにログイン処理を分岐
        if site_name == "場所とる":
            await login_bashotoru(page, creds)
        elif site_name == "スペースラボ":
            await login_spacelab(page, creds)
        elif site_name == "ダイエースペース":
            await login_daiei(page, creds)
        else:
            await login_generic(page, creds)

        # スペースラボは施設名でURLを直接構築
        if site_name == "スペースラボ":
            search_url = f"https://spacelab-system.jp/search/?facility_word={venue_name}"
            await page.goto(search_url, timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=45000)
        else:
            # 検索フォームがあれば使う（なければスキップ）
            search_sel = 'input[placeholder*="施設名"], input[placeholder*="検索"], input[type="search"], input[name*="keyword"]'
            if await page.locator(search_sel).count() > 0:
                await page.locator(search_sel).first.fill(venue_name)
                await page.keyboard.press("Enter")
                await page.wait_for_load_state("networkidle", timeout=45000)

        # 会場リンクを探してクリック
        link = page.locator(f'a:has-text("{venue_name}")').first
        if await link.count() == 0:
            link = page.locator(f'a:has-text("{facility_name}")').first
        if await link.count() == 0:
            return "会場が見つかりません"
        await link.click()
        await page.wait_for_load_state("networkidle", timeout=45000)
        return await scan_calendar(page, start_date_str, end_date_str)
    except Exception as e:
        return f"確認エラー: {str(e)[:120]}"


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
                venue_name = str(venue.get("催事場所名", "") or "")
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
                    venue_copy["確認サイト"] += "（要手動確認あり）"
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
