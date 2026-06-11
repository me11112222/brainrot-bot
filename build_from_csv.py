# -*- coding: utf-8 -*-
"""
ユーザー記入済みCSV(FightTheBRAINROT INDEX - verify_sheet.csv)を正データとして
characters.json を生成する。
- 正しい名前(記入) を採用名にする
- 攻撃力/生産力/価格/入手方法/確率/百鬼 を取り込む
- 画像は BRAINROT/ と名寄せ（正しい名前→OCR名 の順で照合）
"""
import csv
import json
import re
import shutil
import unicodedata
import difflib
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
# 画像ソース（全スキン1869枚）。_1Default のみ使う。
IMG_SRC = Path(r"C:\AI\projects\IndexPng")
# BOT同梱用に、必要な画像だけここへコピー（自己完結＝サーバー移行が楽）
IMG_OUT = BASE / "images"
CSV_IN = BASE / "FightTheBRAINROT INDEX - verify_sheet.csv"
OUT_JSON = BASE / "characters.json"

DEFAULT_RE = re.compile(r"_1default\d*$", re.IGNORECASE)  # _1Default / _1Default1 等
ANYSKIN_RE = re.compile(r"_\d+\w*$")                      # _2Gold / _8Yokai 等のスキン接尾辞

# レア度の改称・統合（課金は廃止しBossへ）
RARITY_REMAP = {"極Boss": "Ultimate Boss", "百鬼": "YokaiBoss", "課金": "Boss"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def build_image_index():
    """default_idx(=_1Defaultスキン) と any_idx(=全スキン代替用) を作る。"""
    default_idx = {}
    any_idx = {}
    for p in sorted(IMG_SRC.glob("*.PNG")) + sorted(IMG_SRC.glob("*.png")):
        s = p.stem
        if s.startswith("T_"):
            s = s[2:]
        if DEFAULT_RE.search(p.stem):
            default_idx[norm(DEFAULT_RE.sub("", s))] = p
        # 代替用: スキン接尾辞を剥がした名前（Defaultが無いキャラ用、最初の1枚を採用）
        base = norm(ANYSKIN_RE.sub("", s))
        if base and base not in any_idx:
            any_idx[base] = p
    return default_idx, any_idx


def match_image(names, default_idx, any_idx):
    """default(完全→部分→曖昧)で照合。見つからなければ他スキンで完全一致のみ代替。"""
    keys = [norm(nm) for nm in names if norm(nm)]
    for key in keys:
        if key in default_idx:
            return default_idx[key], "exact"
    for key in keys:
        for ik, fn in default_idx.items():
            if key in ik or ik in key:
                return fn, "partial"
    for key in keys:
        cand = difflib.get_close_matches(key, list(default_idx.keys()), n=1, cutoff=0.86)
        if cand:
            return default_idx[cand[0]], "fuzzy"
    # Defaultスキンが無いキャラ → 他スキンで代替（完全一致のみ、誤爆防止）
    for key in keys:
        if key in any_idx:
            return any_idx[key], "altskin"
    return None, "none"


def clean_int(v):
    v = str(v).strip()
    m = re.match(r"^(\d+)", v)
    return int(m.group(1)) if m else None


def main():
    default_idx, any_idx = build_image_index()
    # 出力用画像フォルダ（CDN用gitリポなので丸ごと削除しない・差分コピー）
    IMG_OUT.mkdir(parents=True, exist_ok=True)

    chars = []
    stats = {"exact": 0, "partial": 0, "fuzzy": 0, "altskin": 0, "none": 0}

    with open(CSV_IN, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            correct = (row.get("正しい名前(記入)") or "").strip()
            ocr = (row.get("現在の名前(OCR)") or "").strip()
            name = correct or ocr
            if not name:
                continue
            src_path, how = match_image([correct, ocr], default_idx, any_idx)
            stats[how] += 1
            # マッチした画像＋全スキンを BOT 同梱フォルダへコピー
            fn = None
            skins = {}
            if src_path is not None:
                base = re.sub(r"_\d+\w*$", "", src_path.stem)  # 例: T_ShellFish
                variants = (sorted(IMG_SRC.glob(base + "_*.PNG"))
                            + sorted(IMG_SRC.glob(base + "_*.png")))
                if src_path not in variants:
                    variants.append(src_path)
                for sp in variants:
                    m = re.search(r"_\d+([A-Za-z]+)\d*$", sp.stem)
                    skin = m.group(1) if m else "Default"
                    dst = IMG_OUT / sp.name
                    if not dst.exists():
                        shutil.copy2(sp, dst)
                    skins.setdefault(skin, sp.name)
                fn = skins.get("Default") or src_path.name
            tier = (row.get("ティア") or "").strip()
            raw_rarity = (row.get("レア度") or "").strip()
            chars.append({
                "name": name,
                "rarity": RARITY_REMAP.get(raw_rarity, raw_rarity),
                "tier": int(tier) if tier.isdigit() else None,
                "attack": clean_int(row.get("攻撃力")),
                "production": (row.get("生産力") or "").strip(),
                "price": (row.get("価格") or "").strip(),
                "how_to_get": (row.get("入手方法") or "").strip(),
                "drop_rate": (row.get("確率") or "").strip(),
                "hyakki": (row.get("百鬼") or "").strip().upper() == "Y",
                "page": clean_int(row.get("ページ")),
                "order": clean_int(row.get("ページ内順")),
                "image": fn,
                "image_match": how,
                "skins": skins,
            })

    OUT_JSON.write_text(json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"キャラ総数: {len(chars)}")
    print(f"画像マッチ: exact={stats['exact']} partial={stats['partial']} "
          f"fuzzy={stats['fuzzy']} altskin={stats['altskin']} none={stats['none']}")
    rar = {}
    for c in chars:
        rar[c["rarity"]] = rar.get(c["rarity"], 0) + 1
    print("レア度別:", dict(sorted(rar.items(), key=lambda x: -x[1])))
    miss = [c["name"] for c in chars if c["image_match"] == "none"]
    if miss:
        print(f"\n画像なし({len(miss)}体):")
        print("  " + ", ".join(miss))
    fz = [(c["name"], c["image"]) for c in chars if c["image_match"] == "fuzzy"]
    if fz:
        print(f"\n曖昧マッチ({len(fz)}・要確認):")
        for n, f in fz:
            print(f"  {n} -> {f}")
    print(f"\n出力: {OUT_JSON}")


if __name__ == "__main__":
    main()
