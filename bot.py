# -*- coding: utf-8 -*-
"""
ブレインロットファイト 図鑑/戦闘力計算機 Discord BOT (discord.py)

コマンド:
  /index [name]   ... 対話型図鑑。レア度→キャラ選択→スキン切替で
                      画像＆⚔戦闘力＆💰生産力を即再計算。name指定で直接ジャンプ。
  /list   [rarity] [source] ... 一覧（ページ送り）
  /random [rarity]          ... ランダム1体
  /count                    ... レア度別の体数

データ: characters.json（build_from_csv.py で生成）/ 画像: ./images/
計算: スキン(ゲージ)倍率 = base × b（倍率表どおり）。Trait(a)/★Level(c)は今後追加。
"""
import datetime
import json
import os
import re
import random
import time
from collections import defaultdict, deque
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
IMG_DIR = BASE / "images"
DATA = BASE / "characters.json"
# 画像は GitHub→jsDelivr CDN から配信（アップロード不要・無負荷）
CDN_BASE = os.getenv(
    "CDN_BASE", "https://cdn.jsdelivr.net/gh/me11112222/brainrot-images@main/")

load_dotenv(BASE / ".env")
TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = os.getenv("GUILD_ID", "").strip()
# このサーバー以外では一切動かない（追加されても自動退出）
ALLOWED_GUILD = int(GUILD_ID) if GUILD_ID.isdigit() else None
# /panel /unpanel を使える役職名（カンマ区切り・小文字比較）。権限ビットが無い役職でもOKにする
PANEL_ADMIN_ROLES = {r.strip().lower()
                     for r in os.getenv("PANEL_ADMIN_ROLES", "Administrator").split(",")
                     if r.strip()}

RARITY_META = {
    "Common":        {"color": 0x4ade80, "emoji": "🟢", "order": 1},
    "Rare":          {"color": 0x4f8ef7, "emoji": "🔵", "order": 2},
    "Epic":          {"color": 0xa855f7, "emoji": "🟣", "order": 3},
    "Legendary":     {"color": 0xf59e0b, "emoji": "🟡", "order": 4},
    "Mythic":        {"color": 0xef4444, "emoji": "🔴", "order": 5},
    "BrainrotGod":   {"color": 0x22d3ee, "emoji": "🌈", "order": 6},
    "Secret":        {"color": 0xec4899, "emoji": "🤫", "order": 7},
    "Boss":          {"color": 0x1f2937, "emoji": "💀", "order": 8},
    "Ultimate Boss": {"color": 0x7f1d1d, "emoji": "☠️", "order": 9},
    "YokaiBoss":     {"color": 0x991b1b, "emoji": "👹", "order": 10},
    "Missing":       {"color": 0x6b7280, "emoji": "❓", "order": 11},
}

# 変異(Trait): キー → 絵文字。個別に選択可・最大3・効果は同一
TRAITS = [("Patapim", "👃"), ("Hyper", "💑"), ("Hotspot", "📱")]
TRAIT_EMOJI = dict(TRAITS)

# レア度 → ANSI前景色コード（Discordのansiコードブロックで色付き表示できる8色）
# 30:gray 31:red 32:green 33:yellow 34:blue 35:pink 36:cyan 37:white
RARITY_ANSI = {
    "Common": "32", "Rare": "34", "Epic": "35", "Legendary": "33", "Mythic": "31",
    "BrainrotGod": "36", "Secret": "35", "Boss": "37", "Ultimate Boss": "31",
    "YokaiBoss": "31", "Missing": "30",
}

# スパム対策: バースト(短期)＋持続(長期)の二段。違反者は段階的にタイムアウト。
BURST_MAX, BURST_WIN = 6, 4.0            # 4秒に6回まで（連打防止）
SUSTAINED_MAX, SUSTAINED_WIN = 30, 60.0  # 60秒に30回まで（粘着防止）
_rate = defaultdict(deque)
_block_until = {}
_block_count = defaultdict(int)

# Findを押した人の「前回開いた図鑑」インタラクション（押し直しで古いのを消す用）
_user_open = {}

# ── 利用統計（裏で静かにカウント。公開スパムなし）──
USAGE_FILE = BASE / "usage.json"
_usage = {"opens": 0, "users": [], "by_day": {}, "by_char": {}, "by_rarity": {}}
_usage_users = set()
_last_usage_save = 0.0


def _load_usage():
    global _usage, _usage_users
    try:
        _usage = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    for k, d in (("opens", 0), ("users", []), ("by_day", {}),
                 ("by_char", {}), ("by_rarity", {})):
        _usage.setdefault(k, d)
    _usage_users = set(_usage.get("users", []))


def _save_usage(force=False):
    global _last_usage_save
    now = time.monotonic()
    if not force and now - _last_usage_save < 20:
        return  # ディスク書き込みを20秒に1回までに抑制
    _last_usage_save = now
    _usage["users"] = list(_usage_users)
    try:
        USAGE_FILE.write_text(json.dumps(_usage, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def record_open(uid):
    _usage["opens"] += 1
    _usage_users.add(uid)
    today = datetime.date.today().isoformat()
    _usage["by_day"][today] = _usage["by_day"].get(today, 0) + 1
    _save_usage()


def record_view(uid, char):
    _usage_users.add(uid)
    _usage["by_char"][char["name"]] = _usage["by_char"].get(char["name"], 0) + 1
    r = char["rarity"]
    _usage["by_rarity"][r] = _usage["by_rarity"].get(r, 0) + 1
    _save_usage()


_load_usage()


def rate_ok(uid: int) -> bool:
    now = time.monotonic()
    if now < _block_until.get(uid, 0.0):
        return False                         # タイムアウト中は全拒否
    dq = _rate[uid]
    dq.append(now)
    while dq and now - dq[0] > SUSTAINED_WIN:
        dq.popleft()
    burst = sum(1 for t in dq if now - t <= BURST_WIN)
    if burst > BURST_MAX or len(dq) > SUSTAINED_MAX:
        _block_count[uid] += 1
        # 違反のたびに 20秒 → 40秒 …最大5分 のクールダウン
        _block_until[uid] = now + min(300.0, 20.0 * _block_count[uid])
        return False
    return True

# スキン(ゲージ) → ($倍率, ⚔戦闘力倍率)  ※倍率表より
SKIN_ORDER = ["Default", "Gold", "Diamond", "Rainbow", "Angel",
              "Devil", "Royal", "Yokai", "Pirate"]
GAUGE_MULT = {
    "Default": (1.0, 1.0), "Gold": (1.25, 1.1), "Diamond": (1.5, 1.2),
    "Rainbow": (3.0, 1.4), "Angel": (5.0, 1.5), "Devil": (5.0, 1.5),
    "Royal": (6.0, 1.6), "Yokai": (7.0, 1.7), "Pirate": (7.0, 1.7),
}
SKIN_EMOJI = {"Default": "⬜", "Gold": "🟨", "Diamond": "💎", "Rainbow": "🌈",
              "Angel": "😇", "Devil": "😈", "Royal": "👑", "Yokai": "👺", "Pirate": "🏴‍☠️"}

# ★レベル → ($倍率, ⚔戦闘力倍率)
STAR_MULT = {1: (1.5, 1.1), 2: (2.0, 1.25), 3: (2.5, 1.5), 4: (3.0, 2.0), 5: (4.0, 2.8)}

# Trait(属性): 1個につき 戦闘力×+0.1 / 生産×+1.0（効果同一・重複可）
TRAIT_BATTLE_PER = 0.1
TRAIT_MONEY_PER = 1.0
MAX_TRAITS = 3

PER_PAGE = 12
CHAR_SELECT_MAX = 25
CARD_PER = 10        # ②キャラ一覧で1ページに並べるカード数（Embedは1msg最大10）

# ── データ ────────────────────────────────────────
CHARS = json.loads(DATA.read_text(encoding="utf-8"))
BY_NAME = {c["name"].lower(): c for c in CHARS}


def rarity_meta(r):
    return RARITY_META.get(r, {"color": 0x6b7280, "emoji": "▫️", "order": 99})


def sort_key(c):
    return (c.get("page") or 99, c.get("order") or 999)


def rarity_label(c):
    return f"{c['rarity']} T{c['tier']}" if c.get("tier") else c["rarity"]


def source_label(c):
    src = c.get("how_to_get") or "—"
    return f"{src} ({c['drop_rate']})" if c.get("drop_rate") else src


# ── 数値パース/整形 ────────────────────────────────
_UNIT = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_amount(s):
    if s is None:
        return None
    s = str(s).replace("/s", "").strip()
    m = re.match(r"^([\d.]+)\s*([kmbtKMBT]?)", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    u = m.group(2).lower()
    return v * _UNIT.get(u, 1)


def fmt_amount(v, per_s=False):
    suffix = "/s" if per_s else ""
    for u, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            s = f"{v / div:.2f}".rstrip("0").rstrip(".")
            return f"{s}{u}{suffix}"
    return f"{int(round(v)):,}{suffix}"


def scale_amount(s, mult, per_s=False):
    v = parse_amount(s)
    if v is None:
        return s or "—"
    return fmt_amount(v * mult, per_s)


def compute(char, skin="Default", traits=0, star=0):
    """Trait(a)・スキン(b)・★Level(c)を合成した最終ステータスを返す。
    通常: base × a × b / レベル有: base × a × (b + c − 1)
    """
    gm, bm = GAUGE_MULT.get(skin, (1.0, 1.0))           # b
    a_b = 1 + TRAIT_BATTLE_PER * traits                 # a(戦闘)
    a_m = 1 + TRAIT_MONEY_PER * traits                  # a(生産)
    if star in STAR_MULT:
        sm_m, sm_b = STAR_MULT[star]                    # c
        batt_mult = a_b * (bm + sm_b - 1)
        money_mult = a_m * (gm + sm_m - 1)
        price_mult = (gm + sm_m - 1)        # 価格はTrait(a)を掛けない
    else:
        batt_mult = a_b * bm
        money_mult = a_m * gm
        price_mult = gm                     # 価格はTrait(a)を掛けない

    atk = char.get("attack")
    battle = f"{round(atk * batt_mult):,}" if isinstance(atk, (int, float)) else "—"
    prod = scale_amount(char.get("production"), money_mult, per_s=True)
    price = scale_amount(char.get("price"), price_mult, per_s=False)
    return battle, prod, price, batt_mult, money_mult


def avail_skins(char):
    sk = char.get("skins") or {}
    ordered = [s for s in SKIN_ORDER if s in sk]
    # SKIN_ORDER外（Default無くYokaiのみ等）も拾う
    for s in sk:
        if s not in ordered:
            ordered.append(s)
    return ordered


def result_embed(char, skin="Default", trait_keys=None, star=0):
    trait_keys = trait_keys or []
    meta = rarity_meta(char["rarity"])
    battle, prod, price, _b, _m = compute(char, skin, len(trait_keys), star)
    esc = chr(27)
    rc = RARITY_ANSI.get(char["rarity"], "37")
    # 名前をレア度色に。ステータスは1行ずつ色付き（攻撃=赤/生産=黄/価格=緑）
    # 並び順: 名前 → ★(濃縮) → Trait → ⚔/💰/💵
    lines = [f"{esc}[1;{rc}m{char['name']}{esc}[0m"]
    if star:
        lines.append("★" * star)
    if trait_keys:
        lines.append(" ".join(TRAIT_EMOJI.get(k, "") for k in trait_keys))
    lines += [
        f"⚔️ {esc}[1;31m{battle}{esc}[0m",
        f"💰 {esc}[1;33m{prod}{esc}[0m",
        f"💵 {esc}[1;32m{price}{esc}[0m",
    ]
    desc = "```ansi\n" + "\n".join(lines) + "\n```"
    e = discord.Embed(description=desc, color=meta["color"])
    sk = char.get("skins") or {}
    fn = sk.get(skin) or char.get("image")
    if fn:
        e.set_image(url=CDN_BASE + fn)   # CDNから配信（添付なし）
    e.set_footer(text=f"Source: {source_label(char)}")
    return e, None


# ── 対話 View ─────────────────────────────────────
class IndexView(discord.ui.View):
    def __init__(self, char=None):
        super().__init__(timeout=600)
        self.rarity = char["rarity"] if char else None
        self.char = char
        self.skin = "Default"
        self.trait_keys = []      # 選択中のTrait（Patapim/Hyper/Hotspot）
        self.star = 0
        self.page = 0
        self.build()

    def _reset_custom(self):
        self.skin, self.trait_keys, self.star = "Default", [], 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # スパム対策: 連打を弾く
        if not rate_ok(interaction.user.id):
            try:
                await interaction.response.send_message(
                    "⏳ Too fast — slow down a sec!", ephemeral=True)
            except discord.errors.InteractionResponded:
                pass
            return False
        return True

    # 表示中レア度のキャラ一覧
    def rarity_chars(self):
        items = [c for c in CHARS if c["rarity"] == self.rarity]
        items.sort(key=sort_key)
        return items

    def page_count(self):
        n = len(self.rarity_chars())
        return max(1, (n + CARD_PER - 1) // CARD_PER)

    def build(self):
        self.clear_items()
        if self.char is not None:
            # 結果モード: スキン / Trait / ★ 切替＋戻る
            self.add_item(self._skin_select())
            self.add_item(self._trait_select())
            self.add_item(self._star_select())
            self.add_item(self._back_btn())
        else:
            # 参照モード: レア度選択（＋レア度決定後はキャラ選択カード）
            self.add_item(self._rarity_select())
            if self.rarity:
                pages = self.page_count()
                self.page = min(self.page, pages - 1)
                self.add_item(self._char_select())
                if pages > 1:
                    self.add_item(self._page_btn("◀ Prev", -1, self.page <= 0))
                    self.add_item(self._page_btn("Next ▶", +1, self.page >= pages - 1))

    def render(self):
        """現在の状態に応じた edit_message 用 kwargs を返す。"""
        self.build()
        if self.char is not None:
            e, f = result_embed(self.char, self.skin, self.trait_keys, self.star)
            return dict(content=None, embeds=[e],
                        attachments=[f] if f else [], view=self)
        if not self.rarity:
            return dict(content=None, embeds=[self.intro_embed()],
                        attachments=[], view=self)
        content, embeds, files = self.card_payload()
        return dict(content=content, embeds=embeds, attachments=files, view=self)

    async def _refresh(self, interaction):
        await interaction.response.edit_message(**self.render())

    # ②キャラ一覧をカード（サムネ画像）で並べる
    def card_payload(self):
        items = self.rarity_chars()
        pages = self.page_count()
        self.page = min(self.page, pages - 1)
        chunk = items[self.page * CARD_PER:(self.page + 1) * CARD_PER]
        meta = rarity_meta(self.rarity)
        content = (f"{meta['emoji']} **{self.rarity}** ({len(items)})"
                   f"　Page {self.page + 1}/{pages}　— pick a number below")
        embeds, files = [], []
        for i, c in enumerate(chunk, start=self.page * CARD_PER + 1):
            e = discord.Embed(color=meta["color"])
            atk = c.get("attack")
            a = f"⚔{atk:,}" if isinstance(atk, (int, float)) else ""
            e.title = f"{i}. {c['name']}"
            e.description = f"{a}　💰{c.get('production') or ''}"
            fn = c.get("image")
            if fn:
                e.set_thumbnail(url=CDN_BASE + fn)   # CDNサムネ（添付なし）
            embeds.append(e)
        return content, embeds, []

    # --- components ---
    def _rarity_select(self):
        present = sorted({c["rarity"] for c in CHARS}, key=lambda r: rarity_meta(r)["order"])
        opts = [discord.SelectOption(label=r, emoji=rarity_meta(r)["emoji"],
                                     default=(r == self.rarity)) for r in present[:25]]
        sel = discord.ui.Select(placeholder="① Choose a rarity…", options=opts, row=0)

        async def cb(interaction):
            self.rarity = sel.values[0]
            self.page = 0
            await self._refresh(interaction)
        sel.callback = cb
        return sel

    def _char_select(self):
        items = self.rarity_chars()
        chunk = items[self.page * CARD_PER:(self.page + 1) * CARD_PER]
        opts = []
        for i, c in enumerate(chunk, start=self.page * CARD_PER + 1):
            atk = c.get("attack")
            desc = f"⚔{atk:,}" if isinstance(atk, (int, float)) else None
            opts.append(discord.SelectOption(label=f"{i}. {c['name']}"[:100],
                                             value=c["name"], description=desc))
        sel = discord.ui.Select(placeholder="② Pick a character…", options=opts, row=1)

        async def cb(interaction):
            self.char = BY_NAME.get(sel.values[0].lower())
            self._reset_custom()
            if self.char:
                record_view(interaction.user.id, self.char)
            await self._refresh(interaction)
        sel.callback = cb
        return sel

    def _page_btn(self, label, delta, disabled):
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary,
                                row=2, disabled=disabled)

        async def cb(interaction):
            self.page += delta
            await self._refresh(interaction)
        btn.callback = cb
        return btn

    def _skin_select(self):
        opts = [discord.SelectOption(label=s, emoji=SKIN_EMOJI.get(s),
                                     default=(s == self.skin))
                for s in avail_skins(self.char)]
        sel = discord.ui.Select(placeholder="🎨 Skin (gauge)…", options=opts[:25], row=0)

        async def cb(interaction):
            self.skin = sel.values[0]
            await self._refresh(interaction)
        sel.callback = cb
        return sel

    def _trait_select(self):
        opts = [discord.SelectOption(label=k, value=k, emoji=emo,
                                     default=(k in self.trait_keys))
                for k, emo in TRAITS]
        sel = discord.ui.Select(placeholder="✨ Traits (pick any)…", options=opts,
                                min_values=0, max_values=len(TRAITS), row=1)

        async def cb(interaction):
            self.trait_keys = list(sel.values)
            await self._refresh(interaction)
        sel.callback = cb
        return sel

    def _star_select(self):
        opts = [discord.SelectOption(label="No star", value="0", emoji="▫️",
                                     default=(self.star == 0))]
        for n in range(1, 6):
            opts.append(discord.SelectOption(label="★" * n, value=str(n),
                                             default=(self.star == n)))
        sel = discord.ui.Select(placeholder="⭐ Star level…", options=opts, row=2)

        async def cb(interaction):
            self.star = int(sel.values[0])
            await self._refresh(interaction)
        sel.callback = cb
        return sel

    def _back_btn(self):
        btn = discord.ui.Button(label="◀ Back to list", style=discord.ButtonStyle.secondary, row=3)

        async def cb(interaction):
            self.char = None
            self._reset_custom()
            await self._refresh(interaction)
        btn.callback = cb
        return btn

    # 最初の案内Embed（レア度未選択時）
    def intro_embed(self):
        return discord.Embed(
            title="📖 Fight Index",
            description="Pick a **rarity** from the menu below.\n"
                        "(or use `/index <name>` to jump straight to a character)",
            color=0x4f8ef7)


# ── 常駐パネル（チャンネルに置くボタン。押すと本人だけに図鑑が開く）──
class OpenPanelView(discord.ui.View):
    """再起動後も生きる永続View（timeout=None + custom_id）。"""
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not rate_ok(interaction.user.id):
            try:
                await interaction.response.send_message(
                    "⏳ Too fast — slow down a sec!", ephemeral=True)
            except discord.errors.InteractionResponded:
                pass
            return False
        return True

    @discord.ui.button(label="Find", emoji="🔍", style=discord.ButtonStyle.primary,
                       custom_id="brainrot_index_open")
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 同じ人の前回の図鑑を消してから新しく開く（積み上がり防止）
        prev = _user_open.get(interaction.user.id)
        if prev is not None:
            try:
                await prev.delete_original_response()
            except Exception:
                pass
        view = IndexView()
        # ephemeral = 押した本人だけに見える
        await interaction.response.send_message(
            embed=view.intro_embed(), view=view, ephemeral=True)
        _user_open[interaction.user.id] = interaction
        record_open(interaction.user.id)


def panel_embed():
    return discord.Embed(
        title="🔍 Fight Index",
        description="Press **Find** to open your own private index.\n"
                    "Browse by rarity → character → skin / trait / star to see "
                    "Battle Power & Production!",
        color=0x4f8ef7)


# ── BOT本体 ──────────────────────────────────────
class ZukanBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(OpenPanelView())  # 永続ボタンを登録
        if GUILD_ID:
            g = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()


client = ZukanBot()


@client.event
async def on_ready():
    print(f"Logged in: {client.user}  ({len(CHARS)} characters loaded)")
    # 許可サーバー以外に居たら退出（限定運用）
    if ALLOWED_GUILD:
        for g in list(client.guilds):
            if g.id != ALLOWED_GUILD:
                print(f"  leaving unauthorized guild: {g.name} ({g.id})")
                try:
                    await g.leave()
                except Exception:
                    pass


@client.event
async def on_guild_join(guild: discord.Guild):
    # 許可サーバー以外に追加されたら即退出
    if ALLOWED_GUILD and guild.id != ALLOWED_GUILD:
        try:
            await guild.leave()
        except Exception:
            pass


# ── スティッキーパネル（誰か発言したらパネルを最下部へ貼り直す）──
STICKY_FILE = BASE / "sticky.json"
_sticky_channel = None
_last_panel_msg = {}     # channel_id -> Message
_last_repost = {}        # channel_id -> monotonic


def _load_sticky():
    global _sticky_channel
    try:
        _sticky_channel = json.loads(STICKY_FILE.read_text()).get("channel_id")
    except Exception:
        _sticky_channel = None


def _save_sticky():
    try:
        STICKY_FILE.write_text(json.dumps({"channel_id": _sticky_channel}))
    except Exception:
        pass


_load_sticky()


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    if _sticky_channel and message.channel.id == _sticky_channel:
        now = time.monotonic()
        if now - _last_repost.get(message.channel.id, 0) < 2.5:
            return  # 連投時の貼り直し過多を防ぐデバウンス
        _last_repost[message.channel.id] = now
        old = _last_panel_msg.get(message.channel.id)
        if old:
            try:
                await old.delete()
            except Exception:
                pass
        try:
            _last_panel_msg[message.channel.id] = await message.channel.send(
                embed=panel_embed(), view=OpenPanelView())
        except Exception:
            pass


# ── /index ───────────────────────────────────────
async def name_autocomplete(interaction, current: str):
    cur = current.lower()
    hits = [c for c in CHARS if cur in c["name"].lower()]
    hits.sort(key=sort_key)
    return [app_commands.Choice(name=f"{c['name']} ({c['rarity']})"[:100], value=c["name"])
            for c in hits[:25]]


@client.tree.command(name="index", description="Open the Fight Index (rarity → character → skin/trait/star)")
@app_commands.describe(name="Type a character name to jump directly (optional)")
@app_commands.autocomplete(name=name_autocomplete)
async def index_cmd(interaction: discord.Interaction, name: str = None):
    char = None
    if name:
        char = BY_NAME.get(name.lower())
        if not char:
            cand = [c for c in CHARS if name.lower() in c["name"].lower()]
            char = cand[0] if len(cand) == 1 else None
            if not char and cand:
                await interaction.response.send_message(
                    "Multiple matches: " + ", ".join(c["name"] for c in cand[:10]), ephemeral=True)
                return
        if not char:
            await interaction.response.send_message(
                f"\"{name}\" not found.", ephemeral=True)
            return
    record_open(interaction.user.id)
    view = IndexView(char=char)
    if char:
        record_view(interaction.user.id, char)
        e, f = result_embed(char, "Default")
        await interaction.response.send_message(embed=e, view=view, file=f if f else discord.utils.MISSING)
    else:
        await interaction.response.send_message(embed=view.intro_embed(), view=view)


# ── /list ────────────────────────────────────────
class ListView(discord.ui.View):
    def __init__(self, items, title):
        super().__init__(timeout=180)
        self.items, self.title, self.page = items, title, 0
        self.max_page = max(0, (len(items) - 1) // PER_PAGE)
        self._upd()

    def _upd(self):
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.max_page

    def embed(self):
        chunk = self.items[self.page * PER_PAGE:(self.page + 1) * PER_PAGE]
        lines = []
        for c in chunk:
            m = rarity_meta(c["rarity"])
            atk = c.get("attack")
            a = f"⚔{atk:,}" if isinstance(atk, (int, float)) else ""
            lines.append(f"{m['emoji']} **{c['name']}** {a} 💰{c.get('production') or ''}")
        e = discord.Embed(title=self.title, description="\n".join(lines) or "No results",
                          color=0x4f8ef7)
        e.set_footer(text=f"Page {self.page + 1}/{self.max_page + 1} · {len(self.items)} total")
        return e

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction, button):
        self.page = max(0, self.page - 1); self._upd()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        self.page = min(self.max_page, self.page + 1); self._upd()
        await interaction.response.edit_message(embed=self.embed(), view=self)


RARITY_CHOICES = [app_commands.Choice(name=r, value=r) for r in
                  ["Common", "Rare", "Epic", "Legendary", "Mythic", "BrainrotGod",
                   "Secret", "Boss", "Ultimate Boss", "YokaiBoss", "Missing"]]


@client.tree.command(name="list", description="List characters (filter by rarity / source)")
@app_commands.describe(rarity="Rarity", source="How to get (e.g. Demon Box)")
@app_commands.choices(rarity=RARITY_CHOICES)
async def list_cmd(interaction, rarity: app_commands.Choice[str] = None, source: str = None):
    items = list(CHARS)
    title = "📖 Fight Index"
    if rarity:
        items = [c for c in items if c["rarity"] == rarity.value]; title += f" · {rarity.value}"
    if source:
        items = [c for c in items if source.lower() in (c.get("how_to_get") or "").lower()]
        title += f" · {source}"
    items.sort(key=sort_key)
    v = ListView(items, title)
    await interaction.response.send_message(embed=v.embed(), view=v)


@client.tree.command(name="random", description="Pick a random character")
@app_commands.describe(rarity="Rarity (optional)")
@app_commands.choices(rarity=RARITY_CHOICES)
async def random_cmd(interaction, rarity: app_commands.Choice[str] = None):
    pool = [c for c in CHARS if (not rarity or c["rarity"] == rarity.value)]
    if not pool:
        await interaction.response.send_message("No results.", ephemeral=True); return
    c = random.choice(pool)
    record_open(interaction.user.id)
    record_view(interaction.user.id, c)
    e, f = result_embed(c, "Default")
    view = IndexView(char=c)
    await interaction.response.send_message(embed=e, view=view, file=f if f else discord.utils.MISSING)


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        g = interaction.guild
        if g is None:
            return False
        if g.owner_id == interaction.user.id:          # サーバーオーナーは常にOK
            return True
        p = getattr(interaction.user, "guild_permissions", None)
        if p and (p.administrator or p.manage_guild):  # 管理者/サーバー管理 権限
            return True
        # 権限ビットが無くても、許可役職名を持っていればOK
        roles = getattr(interaction.user, "roles", [])
        return any(r.name.lower() in PANEL_ADMIN_ROLES for r in roles)
    return app_commands.check(predicate)


@client.tree.command(name="panel", description="Place a sticky 'Find' button (admin only)")
@app_commands.describe(channel="Channel to place it in (default: here). Use this to target a locked channel.")
@admin_only()
async def panel_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    global _sticky_channel
    target = channel or interaction.channel
    try:
        msg = await target.send(embed=panel_embed(), view=OpenPanelView())
    except discord.Forbidden:
        await interaction.response.send_message(
            f"I can't post in {target.mention}. Give me **View Channel / Send Messages "
            f"/ Embed Links** there, then try again.", ephemeral=True)
        return
    _sticky_channel = target.id
    _last_panel_msg[target.id] = msg
    _save_sticky()
    await interaction.response.send_message(
        f"Panel placed in {target.mention} ✅ (stays pinned to the bottom)", ephemeral=True)


@client.tree.command(name="unpanel", description="Remove the Find panel & stop sticky (admin only)")
@app_commands.describe(channel="Channel to remove it from (default: here)")
@admin_only()
async def unpanel_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    global _sticky_channel
    target = channel or interaction.channel
    cid = target.id
    msg = _last_panel_msg.pop(cid, None)
    if msg:
        try:
            await msg.delete()
        except Exception:
            pass
    if _sticky_channel == cid:
        _sticky_channel = None
        _save_sticky()
    await interaction.response.send_message(
        f"Panel removed from {target.mention}. (Delete the message manually if it "
        f"remains — it won't come back now.)", ephemeral=True)


@panel_cmd.error
@unpanel_cmd.error
async def panel_err(interaction: discord.Interaction, error):
    await interaction.response.send_message(
        "You need the **Manage Server** permission to use this.", ephemeral=True)


@client.tree.command(name="stats", description="Usage stats (admin only)")
@admin_only()
async def stats_cmd(interaction: discord.Interaction):
    today = datetime.date.today().isoformat()
    top = sorted(_usage["by_char"].items(), key=lambda x: -x[1])[:10]
    # 直近7日
    days = sorted(_usage["by_day"].items())[-7:]
    lines = [
        f"🔓 **Opens (total):** {_usage['opens']:,}",
        f"👤 **Unique users:** {len(_usage_users):,}",
        f"📅 **Today:** {_usage['by_day'].get(today, 0):,}",
    ]
    if days:
        lines.append("\n**Last days:** " + " / ".join(f"{d[5:]}:{n}" for d, n in days))
    if top:
        lines.append("\n🏆 **Top characters:**")
        for i, (nm, cnt) in enumerate(top, 1):
            lines.append(f"{i}. {nm} — {cnt:,}")
    e = discord.Embed(title="📊 Usage stats", description="\n".join(lines), color=0x10b981)
    await interaction.response.send_message(embed=e, ephemeral=True)


@stats_cmd.error
async def stats_err(interaction: discord.Interaction, error):
    await interaction.response.send_message(
        "Admin only.", ephemeral=True)


@client.tree.command(name="count", description="Show character counts per rarity")
async def count_cmd(interaction):
    counts = {}
    for c in CHARS:
        counts[c["rarity"]] = counts.get(c["rarity"], 0) + 1
    lines = [f"{rarity_meta(r)['emoji']} **{r}**: {counts[r]}"
             for r in sorted(counts, key=lambda x: rarity_meta(x)["order"])]
    e = discord.Embed(title="📊 Count by rarity", description="\n".join(lines), color=0x10b981)
    e.set_footer(text=f"{len(CHARS)} total")
    await interaction.response.send_message(embed=e)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set (see .env)")
    client.run(TOKEN)
