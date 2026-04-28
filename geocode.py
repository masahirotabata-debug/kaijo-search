import time
import json
import os
import re
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

_geolocator = Nominatim(user_agent="saiji_search")

# キャッシュファイルのパス（geocode.pyと同じフォルダに保存）
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geocode_cache.json")

def _load_cache() -> dict:
    """JSONファイルからキャッシュを読み込む。"""
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_cache(cache: dict):
    """キャッシュをJSONファイルに保存する。"""
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# 起動時にキャッシュを読み込む
_cache = _load_cache()

# CSV 1行目の都道府県短縮形 → 正式名
_PREF_NORMALIZE = {
    "東京": "東京都",
    "神奈川": "神奈川県",
    "埼玉": "埼玉県",
    "千葉": "千葉県",
    "茨城": "茨城県",
    "栃木": "栃木県",
    "群馬": "群馬県",
    "山梨": "山梨県",
    "長野": "長野県",
    "新潟": "新潟県",
}

# 市区町村を抽出する正規表現
_CITY_PATTERN = re.compile(
    r'^(東京都|北海道|(?:京都|大阪)府|.{2,4}県)?'
    r'(.+?(?:市|区|町|村))'
)

def normalize_prefecture(pref: str) -> str:
    """短縮都道府県名を正式名に変換する（例: 東京 → 東京都）。"""
    return _PREF_NORMALIZE.get(pref.strip(), pref.strip())

def extract_city(address: str) -> str:
    """
    住所から都道府県＋市区町村だけを抽出して返す。
    例: '町田市藤の台一丁目1番54号1-54' → '町田市'
    例: '福生市武蔵野台1-9-8' → '福生市'
    抽出できない場合はそのまま返す。
    """
    address = address.strip()
    match = _CITY_PATTERN.match(address)
    if match:
        pref = match.group(1) or ""
        city = match.group(2) or ""
        return (pref + city).strip()
    return address

def geocode_address(address: str, retries: int = 3) -> tuple | None:
    """
    住所を緯度経度に変換する。
    市区町村レベルに簡略化してからNominatimに問い合わせる。
    キャッシュがあればそれを返す。失敗時は None を返す。
    """
    # 市区町村レベルに簡略化
    simplified = extract_city(address)

    # キャッシュに存在すればすぐ返す
    if simplified in _cache:
        cached = _cache[simplified]
        if cached is None:
            return None
        return tuple(cached)

    # Nominatimに問い合わせ（簡略化した住所で検索）
    for attempt in range(retries):
        try:
            location = _geolocator.geocode(simplified + "、日本", timeout=10)
            if location:
                result = (location.latitude, location.longitude)
                _cache[simplified] = list(result)
                _save_cache(_cache)
                return result
            # 見つからなかった場合もキャッシュしてNoneを返す
            _cache[simplified] = None
            _save_cache(_cache)
            return None
        except GeocoderTimedOut:
            if attempt < retries - 1:
                time.sleep(1)
        except GeocoderServiceError:
            if attempt < retries - 1:
                time.sleep(1)

    return None

def calculate_distance_km(coord1: tuple, coord2: tuple) -> float:
    """2点間の距離をkmで返す。"""
    return geodesic(coord1, coord2).km
