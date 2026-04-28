import subprocess
import sys
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)


"""
催事場所検索ツール - STEP1: 住所×半径検索 / STEP2: 発注先別分類 / STEP3: 空き確認
"""

import io
import json
import os
import subprocess
import sys

import pandas as pd
import streamlit as st
from geopy.distance import geodesic
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

from geocode import geocode_address

VENUES_JSON = os.path.join(os.path.dirname(__file__), "venues.json")
WORKER_SCRIPT = os.path.join(os.path.dirname(__file__), "availability_worker.py")

DISPLAY_COLUMNS = [
    "施設名", "実施場所名", "施設住所", "広さ",
    "屋内・屋外", "金額（平日）", "金額（土日祝日）",
    "通信催事可否", "発注先",
]

RADIUS_OPTIONS = [3, 5, 10, 20, 25]

FIELD_MAP = {
    "brand": "施設名", "store": "実施場所名", "addr": "施設住所",
    "spot": "広さ", "inout": "屋内・屋外", "feeW": "金額（平日）",
    "feeE": "金額（土日祝日）", "comm": "通信催事可否", "supplier": "発注先",
    "pref": "都道府県", "muni": "市区町村", "lat": "lat", "lng": "lng",
}

SUPPORTED_SITES = ["場所とる", "スペースラボ", "楽市", "自由市場", "ダイエースペース"]
MANUAL_KEYWORDS = ["グラスト", "グラスと", "アイフィールド", "ライフ", "ネクサスイノベーション"]


@st.cache_data(show_spinner="会場データを読み込み中...")
def load_venues() -> pd.DataFrame:
    with open(VENUES_JSON, "r", encoding="utf-8") as f:
        venues = json.load(f)
    return pd.DataFrame([{FIELD_MAP[k]: v.get(k, "") for k in FIELD_MAP if k in v} for v in venues])


def filter_by_distance(df, origin_coord, radius_km):
    def in_radius(row):
        try:
            return geodesic(origin_coord, (float(row["lat"]), float(row["lng"]))).km <= radius_km
        except Exception:
            return False
    return df[df.apply(in_radius, axis=1)].copy()


def df_to_excel_bytes(df):
    wb = Workbook()
    ws = wb.active
    ws.title = "会場リスト"
    for row in dataframe_to_rows(df, index=False, header=True):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def show_download_buttons(df, prefix="会場リスト"):
    col1, col2 = st.columns(2)
    with col1:
        st.download_button("📥 CSV ダウンロード",
            data=df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name=f"{prefix}.csv", mime="text/csv")
    with col2:
        st.download_button("📊 Excel ダウンロード",
            data=df_to_excel_bytes(df),
            file_name=f"{prefix}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def classify_row(supplier):
    s = str(supplier)
    if not s or s.strip() in ["", "nan"]:
        return "不明"
    for site in SUPPORTED_SITES:
        if site in s:
            return site
    for kw in MANUAL_KEYWORDS:
        if kw in s:
            return "手動確認要"
    return "その他"


def run_availability_worker(venues_list, start_date, end_date):
    """別プロセスでPlaywrightを実行して空き確認する（ファイル経由）。"""
    import tempfile

    payload = {
        "venues": venues_list,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f_in:
        json.dump(payload, f_in, ensure_ascii=False)
        input_path = f_in.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f_out:
        output_path = f_out.name

    try:
        result = subprocess.run(
            [sys.executable, WORKER_SCRIPT, input_path, output_path],
            capture_output=True, text=True, encoding="utf-8", timeout=600
        )
        if result.returncode != 0:
            raise RuntimeError(f"ワーカーエラー:\n{result.stderr[:500]}")
        with open(output_path, "r", encoding="utf-8") as f:
            return json.load(f)
    finally:
        try:
            os.unlink(input_path)
            os.unlink(output_path)
        except Exception:
            pass


def show_avail_results(results):
    """空き確認結果を表示する。"""
    available = [r for r in results if not r.get("_excluded") and not r.get("要手動確認")]
    manual = [r for r in results if r.get("要手動確認") and not r.get("_excluded")]
    excluded = [r for r in results if r.get("_excluded")]

    st.divider()
    st.header("空き確認結果")

    st.subheader(f"✅ 空きあり — {len(available)} 件")
    if available:
        avail_df = pd.DataFrame(available)
        cols = [c for c in DISPLAY_COLUMNS + ["確認サイト", "確認結果"] if c in avail_df.columns]
        st.dataframe(avail_df[cols], use_container_width=True)
        show_download_buttons(avail_df[cols], prefix="空きあり会場")
    else:
        st.info("空きありの会場はありませんでした。")

    if manual:
        st.subheader(f"⚠️ 手動確認要 — {len(manual)} 件")
        manual_df = pd.DataFrame(manual)
        cols = [c for c in DISPLAY_COLUMNS + ["確認サイト", "確認結果"] if c in manual_df.columns]
        st.dataframe(manual_df[cols], use_container_width=True)
        show_download_buttons(manual_df[cols], prefix="手動確認要会場")

    if excluded:
        with st.expander(f"❌ 予約済みで除外 — {len(excluded)} 件"):
            excl_df = pd.DataFrame(excluded)
            cols = [c for c in DISPLAY_COLUMNS + ["確認サイト", "確認結果"] if c in excl_df.columns]
            st.dataframe(excl_df[cols], use_container_width=True)


def main():
    st.set_page_config(page_title="催事場所検索ツール", page_icon="🗺️", layout="wide")
    st.title("🗺️ 催事場所検索ツール")

    all_venues_df = load_venues()
    if all_venues_df.empty:
        st.error("venues.json が見つかりません。")
        return

    st.caption(f"読み込み済み会場数: {len(all_venues_df):,} 件")

    # ══════════════════════════════════════════════
    # STEP 1: 住所 × 半径検索
    # ══════════════════════════════════════════════
    st.header("STEP 1：住所 × 半径で会場を検索")

    with st.form("search_form"):
        address_input = st.text_input("検索する住所を入力", placeholder="例: 東京都渋谷区渋谷1-1-1")
        radius_km = st.selectbox("半径を選択", options=RADIUS_OPTIONS, format_func=lambda x: f"{x} km")
        search_btn = st.form_submit_button("🔍 会場を検索", use_container_width=True)

    if search_btn:
        if not address_input.strip():
            st.error("住所を入力してください。")
        else:
            with st.spinner("住所をジオコーディング中..."):
                origin = geocode_address(address_input.strip())
            if origin is None:
                st.error("住所のジオコーディングに失敗しました。")
            else:
                st.info(f"📍 検索基点: 緯度 {origin[0]:.5f}, 経度 {origin[1]:.5f}")
                filtered_df = filter_by_distance(all_venues_df, origin, float(radius_km))
                display_cols = [c for c in DISPLAY_COLUMNS if c in filtered_df.columns]
                st.session_state["filtered_df"] = filtered_df
                st.session_state["display_cols"] = display_cols
                st.session_state["search_done"] = True
                st.session_state.pop("classified", None)
                st.session_state.pop("avail_results", None)

    if st.session_state.get("search_done") and "filtered_df" in st.session_state:
        filtered_df = st.session_state["filtered_df"]
        display_cols = st.session_state["display_cols"]

        st.success(f"✅ {len(filtered_df)} 件の会場が見つかりました。")
        if not filtered_df.empty:
            st.dataframe(filtered_df[display_cols], use_container_width=True)
            show_download_buttons(filtered_df[display_cols], prefix="会場リスト_半径検索")

            # ══════════════════════════════════════════════
            # STEP 2: 発注先別分類
            # ══════════════════════════════════════════════
            st.divider()
            st.header("STEP 2：発注先別に会場を分類")

            if st.button("📋 発注先別に分類する", use_container_width=True):
                filtered_df["発注先分類"] = filtered_df["発注先"].apply(lambda x: classify_row(str(x)))
                st.session_state["classified_df"] = filtered_df
                st.session_state["classified"] = True

    if st.session_state.get("classified") and "classified_df" in st.session_state:
        classified_df = st.session_state["classified_df"]
        display_cols_cls = [c for c in DISPLAY_COLUMNS + ["発注先分類"] if c in classified_df.columns]

        st.divider()
        st.subheader(f"📋 全会場一覧 — {len(classified_df)} 件")
        st.dataframe(classified_df[display_cols_cls], use_container_width=True)
        show_download_buttons(classified_df[display_cols_cls], prefix="全会場_発注先分類")

        for site in SUPPORTED_SITES:
            site_df = classified_df[classified_df["発注先分類"] == site]
            if not site_df.empty:
                st.subheader(f"🏪 {site} — {len(site_df)} 件")
                st.dataframe(site_df[display_cols_cls], use_container_width=True)
                show_download_buttons(site_df[display_cols_cls], prefix=f"会場_{site}")

        manual_df = classified_df[classified_df["発注先分類"] == "手動確認要"]
        if not manual_df.empty:
            st.subheader(f"⚠️ 手動確認要 — {len(manual_df)} 件")
            st.dataframe(manual_df[display_cols_cls], use_container_width=True)
            show_download_buttons(manual_df[display_cols_cls], prefix="会場_手動確認要")

        other_df = classified_df[classified_df["発注先分類"].isin(["その他", "不明"])]
        if not other_df.empty:
            with st.expander(f"❓ その他・不明 — {len(other_df)} 件"):
                st.dataframe(other_df[display_cols_cls], use_container_width=True)
                show_download_buttons(other_df[display_cols_cls], prefix="会場_その他")

        # ══════════════════════════════════════════════
        # STEP 3: 期間指定で空き確認
        # ══════════════════════════════════════════════
        st.divider()
        st.header("STEP 3：期間を指定して空き確認")

        # 確認方法の選択
        check_mode = st.radio(
            "確認する会場の選択方法",
            options=[
                "1. 対応サイト全て（STEP2のリストから自動対応サイトを全件確認）",
                "2. 対応サイトを選択（複数選択可）",
                "3. CSVまたはExcelをアップロードして確認",
            ],
            key="check_mode"
        )

        target_df = pd.DataFrame()

        # ── モード1: 全件 ──
        if check_mode.startswith("1"):
            target_df = classified_df[classified_df["発注先分類"].isin(SUPPORTED_SITES)]
            st.info(f"対象会場: {len(target_df)} 件（対応サイト全て）")

        # ── モード2: サイト選択 ──
        elif check_mode.startswith("2"):
            available_sites = [
                s for s in SUPPORTED_SITES
                if s in classified_df["発注先分類"].values
            ]
            selected_sites = st.multiselect(
                "確認するサイトを選択（複数可）",
                options=available_sites,
                default=available_sites[:1] if available_sites else [],
            )
            if selected_sites:
                target_df = classified_df[classified_df["発注先分類"].isin(selected_sites)]
                st.info(f"対象会場: {len(target_df)} 件")
            else:
                st.warning("サイトを1つ以上選択してください。")

        # ── モード3: アップロード ──
        elif check_mode.startswith("3"):
            st.markdown("""
**手順：**
1. STEP1またはSTEP2のリストをCSV/Excelでダウンロード
2. ダウンロードしたファイルを開いて確認したい会場だけ残す
3. 保存してここにアップロード
            """)
            uploaded_file = st.file_uploader(
                "CSVまたはExcelファイルをアップロード",
                type=["csv", "xlsx"],
                key="upload_file"
            )
            if uploaded_file:
                try:
                    if uploaded_file.name.endswith(".csv"):
                        target_df = pd.read_csv(uploaded_file, encoding="utf-8-sig")
                    else:
                        target_df = pd.read_excel(uploaded_file)
                    st.success(f"✅ {len(target_df)} 件読み込みました。")
                    cols = [c for c in DISPLAY_COLUMNS if c in target_df.columns]
                    st.dataframe(target_df[cols], use_container_width=True)
                except Exception as e:
                    st.error(f"ファイル読み込みエラー: {e}")

        # ── 日付入力と実行 ──
        if not target_df.empty:
            with st.form("availability_form"):
                col1, col2 = st.columns(2)
                with col1:
                    start_date = st.date_input("開始日")
                with col2:
                    end_date = st.date_input("終了日")
                avail_btn = st.form_submit_button("📅 空き状況を確認する", use_container_width=True)

            if avail_btn:
                if end_date < start_date:
                    st.error("終了日は開始日以降を指定してください。")
                else:
                    venues_list = target_df.to_dict(orient="records")
                    with st.spinner(f"空き確認中... {len(venues_list)} 件（しばらくお待ちください）"):
                        try:
                            results = run_availability_worker(venues_list, start_date, end_date)
                            st.session_state["avail_results"] = results
                        except Exception as e:
                            st.error(f"エラーが発生しました: {e}")

    # 空き確認結果表示
    if "avail_results" in st.session_state:
        show_avail_results(st.session_state["avail_results"])


if __name__ == "__main__":
    main()
