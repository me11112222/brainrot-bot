# -*- coding: utf-8 -*-
"""
331MonsterType一覧.txt を解析して characters.json を生成する。
- キャラ名 / MonsterTypeID / レア度 / 分類カテゴリ / メモ(配信者名など) / 画像ファイル名
を抽出し、BRAINROT/ 内の画像と名寄せする。

ステータス/効果/入手方法 の枠も用意しておき、後から stats_override.json で上書きできる。
"""
import json
import re
import unicodedata
import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # event-tool/
SRC_TXT = ROOT / "331MonsterType一覧.txt"
IMG_DIR = ROOT / "BRAINROT"
OUT_JSON = Path(__file__).resolve().parent / "characters.json"
STATS_OVERRIDE = Path(__file__).resolve().parent / "stats_override.json"

# レア度として認識するキーワード（正規化キー: 表示名）
RARITY_KEYWORDS = {
    "common": "Common",
    "rare": "Rare",
    "epic": "Epic",
    "legendary": "Legendary",
    "mythic": "Mythic",
    "brainrotgod": "BrainrotGod",
    "secret": "Secret",
    "boss": "Boss",
    "admin": "Admin",
    "thunder": "Thunder",
}

# セクション見出し（#N.Rarity 形式や特殊見出し）からレア度/カテゴリを拾う
SECTION_RE = re.compile(r"#\s*\d*\.?\s*([A-Za-z]+)")

# 画像名寄せ用の別名（enum名 -> 画像が使っている名前）
NAME_ALIASES = {
    "SixSeven": "67",
    "SixSixSix": "666",
}

# ブロック/アイテム系（キャラ画像を持たない仕様要素）の判定キーワード
BLOCK_KEYWORDS = ("luckyblock", "luckybag", "adminblock", "valentineblock", "brrbrrbox")


def norm(s: str) -> str:
    """名寄せ用の正規化: 全角→半角、英数字小文字のみ残す。"""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def build_image_index():
    """画像stemの正規化キー -> ファイル名 の辞書を作る。"""
    idx = {}
    for p in sorted(IMG_DIR.glob("*.PNG")) + sorted(IMG_DIR.glob("*.png")):
        stem = p.stem  # T_BizzyFishy_1Default
        s = stem
        if s.startswith("T_"):
            s = s[2:]
        # 末尾の _1Default / _1Default1 などを除去
        s = re.sub(r"_?1default\d*$", "", s, flags=re.IGNORECASE)
        idx[norm(s)] = p.name
    return idx


def match_image(name: str, img_index: dict):
    """完全一致 → 部分一致 → 曖昧一致 の順で画像を探す。"""
    key = norm(NAME_ALIASES.get(name, name))
    if key in img_index:
        return img_index[key], "exact"
    # 部分一致（綴り揺れ吸収）
    for ik, fn in img_index.items():
        if key and (key in ik or ik in key):
            return fn, "partial"
    # 曖昧一致
    cand = difflib.get_close_matches(key, list(img_index.keys()), n=1, cutoff=0.82)
    if cand:
        return img_index[cand[0]], "fuzzy"
    return None, "none"


def parse_txt():
    lines = SRC_TXT.read_text(encoding="utf-8").splitlines()
    chars = []
    current_section = None  # 見出し由来のレア度/カテゴリ
    current_category = None  # 配信者様/Valentine/Player考案 など

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()

        # enum宣言・型定義などの行はスキップ
        if ":=" in stripped or "<public>" in stripped or stripped.lower().startswith("monster_type"):
            continue

        # コメントのみの行（見出し）
        if stripped.startswith("#"):
            low = stripped.lower()
            # 特殊カテゴリ見出し
            if "配信者" in stripped:
                current_category = "配信者様"
                current_section = "Secret"
                continue
            if "valentine" in low or "バレンタイン" in stripped:
                current_category = "Valentine"
                continue
            if "player考案" in low or "player 考案" in low or "プレイヤー考案" in stripped:
                current_category = "Player考案"
                continue
            if "luckyba" in low or "luckyblock" in low or "locky block" in low:
                current_category = "LuckyBlock"
                continue
            # 通常のレア度見出し  例: #1.Common / #5.Mythic / #7.Secret / #8.Boss
            m = SECTION_RE.search(stripped)
            if m:
                kw = m.group(1).lower()
                if kw in RARITY_KEYWORDS:
                    current_section = RARITY_KEYWORDS[kw]
                    current_category = None
            continue

        # データ行: Name #ID  #Rarity #note...
        parts = stripped.split("#")
        name = parts[0].strip().rstrip("★").strip()
        if not name:
            continue
        comments = [c.strip() for c in parts[1:] if c.strip()]

        mtype_id = None
        rarity = None
        notes = []
        category = current_category

        for c in comments:
            # ID（数字のみ、先頭が数字）
            mnum = re.match(r"^(\d+)\b", c)
            if mtype_id is None and mnum:
                mtype_id = int(mnum.group(1))
                rest = c[mnum.end():].strip()
                if rest:
                    comments.append(rest)  # ID後の残りも後で処理
                continue
            # レア度キーワード判定
            cl = norm(c)
            matched_rarity = None
            for kw, disp in RARITY_KEYWORDS.items():
                if cl.startswith(kw):
                    matched_rarity = disp
                    break
            if matched_rarity and rarity is None:
                rarity = matched_rarity
                # 例: "Commonで登録すること..." のような補足はメモにも残す
                if len(c) > len(matched_rarity) + 2:
                    notes.append(c)
                continue
            if "配信者" in c:
                category = "配信者様"
                rarity = rarity or "Secret"
                continue
            notes.append(c)

        if rarity is None:
            rarity = current_section or "Unknown"

        # ブロック/アイテム系（キャラ画像を持たない仕様要素）の判定
        nkey = norm(name)
        is_block = any(b in nkey for b in BLOCK_KEYWORDS) or "luckybag" in nkey
        if is_block and not category:
            category = "Block/Item"

        chars.append({
            "name": name,
            "monster_type_id": mtype_id,
            "rarity": rarity,
            "category": category,
            "is_block": is_block,
            "note": " / ".join(notes) if notes else "",
        })
    return chars


def main():
    if not SRC_TXT.exists():
        raise SystemExit(f"見つからない: {SRC_TXT}")
    img_index = build_image_index()
    chars = parse_txt()

    # 画像名寄せ
    img_stats = {"exact": 0, "partial": 0, "fuzzy": 0, "none": 0, "block": 0}
    for c in chars:
        if c.get("is_block"):
            c["image"] = None
            c["image_match"] = "block"
            img_stats["block"] += 1
            continue
        fn, how = match_image(c["name"], img_index)
        c["image"] = fn
        c["image_match"] = how
        img_stats[how] += 1

    # ステータス上書きの読み込み（あれば）
    overrides = {}
    if STATS_OVERRIDE.exists():
        overrides = json.loads(STATS_OVERRIDE.read_text(encoding="utf-8"))

    # ステータス枠を付与（後から埋められる）
    for c in chars:
        ov = overrides.get(c["name"], {}) or overrides.get(str(c["monster_type_id"]), {})
        c["stats"] = ov.get("stats", {})            # 例: {"income": 100, "speed": 1.5}
        c["effect"] = ov.get("effect", "")          # 効果テキスト
        c["how_to_get"] = ov.get("how_to_get", "")  # 入手方法

    OUT_JSON.write_text(json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8")

    # レポート
    print(f"キャラ総数: {len(chars)}")
    print(f"画像マッチ: exact={img_stats['exact']} partial={img_stats['partial']} "
          f"fuzzy={img_stats['fuzzy']} none={img_stats['none']} block={img_stats['block']}")
    rarities = {}
    for c in chars:
        rarities[c["rarity"]] = rarities.get(c["rarity"], 0) + 1
    print("レア度別:", dict(sorted(rarities.items(), key=lambda x: -x[1])))
    miss = [c["name"] for c in chars if c["image_match"] == "none"]
    if miss:
        print(f"画像なし({len(miss)}):", ", ".join(miss))
    fuzzy = [(c["name"], c["image"]) for c in chars if c["image_match"] == "fuzzy"]
    if fuzzy:
        print("曖昧マッチ(要確認):")
        for n, f in fuzzy:
            print(f"  {n} -> {f}")
    print(f"\n出力: {OUT_JSON}")


if __name__ == "__main__":
    main()
