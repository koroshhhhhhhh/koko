"""ربات تلگرام بازی مسابقهٔ ماشین 🏎️

ساختار این فایل:
  ۱) متن‌ها و رندر UI  (فقط تبدیل وضعیت بازی به پیام تلگرام)
  ۲) هندلرهای گروه   (/race، لابی، کنترل دنده)
  ۳) اجرای مسابقه    (شمارش معکوس، حلقهٔ بروزرسانی، پایان و جایزه)
  ۴) هندلرهای چت خصوصی (راهنما، ماشین من، موجودی)
منطق بازی در engine.py و دیتابیس در database.py است.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time

from telegram import (
    BotCommand,
    InlineKeyboardMarkup as Markup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config as C
from colors import styled_button as Btn  # دکمه‌های رنگی (سبز/قرمز/آبی)
from database import Database
from engine import CarStats, EventKind, Phase, Race, ShiftResult

log = logging.getLogger("racebot")

DB: Database  # در main() ساخته می‌شود

# ─────────────────────────── وضعیت حافظه‌ای مسابقه‌ها ───────────────────────────
RACES: dict[int, Race] = {}       # race_id -> Race
CHAT_RACE: dict[int, int] = {}    # chat_id -> race_id (هر گروه فقط یک مسابقهٔ فعال)
TASKS: set[asyncio.Task] = set()

# ═══════════════════════════ ۱) متن‌ها و رندر UI ═══════════════════════════
MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
CARS = ["🏎️", "🚗", "🚙", "🚕", "🚓", "🚐", "🛻", "🚑", "🚒", "🚚"]
LINE = "━━━━━━━━━━━━━━"

BTN_GUIDE = "📖 راهنمای بازی"
BTN_CAR = "🏎️ ماشین من"
BTN_BALANCE = "💰 موجودی من"
MAIN_KEYBOARD = ReplyKeyboardMarkup([[BTN_GUIDE], [BTN_CAR, BTN_BALANCE]], resize_keyboard=True)

WELCOME = (
    "🏁 <b>به بازی مسابقهٔ ماشین خوش آمدی!</b>\n\n"
    "اینجا قراره با ماشینت با بقیهٔ بازیکن‌ها مسابقه بدی،\n"
    "دنده عوض کنی، سرعت بگیری و برای رتبهٔ اول بجنگی! 🏎️💨\n\n"
    "برای اینکه سریع بازی رو یاد بگیری:\n"
    "روی «📖 راهنمای بازی» بزن."
)
WELCOME_BACK = "🏁 خوش برگشتی! از منوی پایین استفاده کن. 👇"
PRIVATE_RACE = "❌ مسابقه فقط داخل گروه‌ها قابل اجراست.\n\nیک گروه را انتخاب کن و آنجا /race را اجرا کن."
NOT_YOUR_PANEL = "❌ این پنل برای شما نیست."
NOT_YOUR_CONTROL = "❌ این کنترل برای شما نیست."
HOST_ONLY = "❌ این بخش فقط برای سازندهٔ مسابقه است."


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def fmt(n: int) -> str:
    return f"{n:,}"


def short(name: str, n: int = 9) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


def user_name(user) -> str:
    return (user.first_name or user.username or str(user.id))[:20]


# ── پنل ساخت مسابقه ──
def panel_text() -> str:
    return "🏁 <b>مسابقه</b>\n\nآیا تمایل به ساخت یک مسابقه دارید؟"


def panel_kb(uid: int) -> Markup:
    return Markup([[Btn("🏎️ ساخت مسابقه", callback_data=f"rc:c:{uid}"),
                    Btn("❌ لغو", callback_data=f"rc:x:{uid}")]])


def _number_rows(callback_prefix: str, current: int | None = None) -> list[list[Btn]]:
    numbers = list(range(C.MIN_PLAYERS, C.MAX_PLAYERS + 1))
    rows, sizes, i = [], [4, 3, 2], 0
    while i < len(numbers):
        size = sizes[min(len(rows), len(sizes) - 1)]
        rows.append([Btn(f"✅ {n}" if n == current else str(n), callback_data=f"{callback_prefix}:{n}")
                     for n in numbers[i:i + size]])
        i += size
    return rows


def capacity_text() -> str:
    return "👥 <b>تعداد بازیکنان را انتخاب کنید:</b>"


# ── لابی ──
def render_lobby(race: Race) -> str:
    lines = ["🏁 <b>لابی مسابقه</b>", "",
             f"👥 ظرفیت: {len(race.players)} / {race.max_players}", "", "🏎️ بازیکنان:", ""]
    players = list(race.players.values())
    for i in range(race.max_players):
        lines.append(f"{MEDALS[i]} {esc(players[i].name) if i < len(players) else '-'}")
    lines += ["", LINE, ""]
    lines.append("✅ ظرفیت مسابقه تکمیل شد!" if race.is_full else "⏳ منتظر بازیکنان...")
    host = race.players.get(race.host_id)
    lines += ["", f"سازنده: {esc(host.name) if host else '-'}"]
    return "\n".join(lines)


def lobby_kb(race: Race) -> Markup:
    r = race.race_id
    return Markup([
        [Btn("🏎️ پیوستن", callback_data=f"lb:j:{r}"), Btn("🚪 خروج", callback_data=f"lb:l:{r}")],
        [Btn("⚙️ تنظیمات مسابقه", callback_data=f"lb:s:{r}"), Btn("🚦 شروع مسابقه", callback_data=f"lb:g:{r}")],
    ])


def render_settings(race: Race) -> str:
    return (f"⚙️ <b>تنظیمات مسابقه</b>\n\n👥 بازیکنان فعلی: {len(race.players)}\n"
            f"📊 ظرفیت فعلی: {race.max_players}\n\nظرفیت جدید را انتخاب کنید:\n"
            f"(حداقل: {max(C.MIN_PLAYERS, len(race.players))}  |  حداکثر: {C.MAX_PLAYERS})")


def settings_kb(race: Race) -> Markup:
    rows = _number_rows(f"lb:c:{race.race_id}", race.max_players)
    rows.append([Btn("🗑️ لغو مسابقه", callback_data=f"lb:k:{race.race_id}"),
                 Btn("🔙 بازگشت", callback_data=f"lb:b:{race.race_id}")])
    return Markup(rows)


def render_confirm(race: Race) -> str:
    return (f"⚠️ <b>آیا مسابقه را شروع می‌کنید؟</b>\n\n👥 بازیکنان: {len(race.players)}\n"
            f"🏁 مقصد: {race.distance}m")


def confirm_kb(race: Race) -> Markup:
    return Markup([[Btn("✅ شروع", callback_data=f"lb:y:{race.race_id}"),
                    Btn("❌ لغو", callback_data=f"lb:b:{race.race_id}")]])


# ── مسابقه ──
def rpm_bar(p, cells: int = 14) -> str:
    """نوار RPM: 🟦 پایین | 🟩 محدودهٔ تعویض عالی | 🟥 بیش از حد | دایره = نشانگر (رنگ دایره = ناحیهٔ فعلی)"""
    per = C.REDLINE_RPM / cells
    win = p.window()
    kinds = []
    for i in range(cells):
        lo, hi = i * per, (i + 1) * per
        if win is None:
            kinds.append("🟥" if (lo + hi) / 2 >= 0.88 * C.REDLINE_RPM else "🟦")
            continue
        overlap = max(0.0, min(hi, win[1]) - max(lo, win[0])) / per
        kinds.append("🟩" if overlap >= 0.5 else ("🟥" if lo >= win[1] else "🟦"))
    if win is not None and "🟩" not in kinds:
        kinds[min(cells - 1, int((win[0] + win[1]) / 2 / per))] = "🟩"
    idx = min(cells - 1, int(p.rpm / per))
    kinds[idx] = {"🟦": "🔵", "🟩": "🟢", "🟥": "🔴"}[kinds[idx]]
    return "".join(kinds)


def track_bar(dist: float, total: int, idx: int, cells: int = 12) -> str:
    pos = min(cells, int(dist / total * cells))
    return "━" * pos + CARS[idx % len(CARS)] + "┄" * (cells - pos) + "🏁"


def event_text(race: Race, e) -> str:
    a = race.players.get(e.player_id)
    b = race.players.get(e.other_id)
    an, bn = (esc(a.name) if a else ""), (esc(b.name) if b else "")
    if e.kind == EventKind.GO:
        return "🚦 شروع!"
    if e.kind == EventKind.PERFECT:
        return f"🔥 {an} تعویض عالی زد!"
    if e.kind == EventKind.MISSED:
        return f"⚠️ {an} تعویض را از دست داد"
    if e.kind == EventKind.OVERTAKE:
        return f"🔥 {an} از {bn} سبقت گرفت!"
    if e.kind == EventKind.FINISH:
        return f"🏆 {an} در رتبهٔ {e.value} به خط پایان رسید!"
    return ""


def render_race(race: Race, now: float) -> str:
    ranking = race.ranking()
    lead = max(p.distance for p in ranking)
    lines = [
        f"🏁 <b>مسابقه</b>   📍 {int(lead)} / {race.distance}m   ⏱ {int(now - race.start_time)}ث",
        "🟦 پایین   🟩 لحظهٔ تعویض   🟥 بیش از حد",
        "",
    ]
    for p in ranking:
        rank = p.finish_position or p.position or 1
        medal = MEDALS[min(rank, len(MEDALS)) - 1]
        stats = f"🔥{p.perfect_shifts} ⚠️{p.missed_shifts}"
        if p.finish_position is not None:
            when = f" · ⏱ {p.finish_time:.1f}ث" if p.finish_time else ""
            lines += [f"{medal} <b>{esc(short(p.name, 14))}</b> · 🏁 پایان{when} · {stats}", ""]
            continue
        state = p.shift_state(now)
        hint = {"green": f"🟢 الان! ⚡ دنده {p.gear + 1}", "late": f"🔴 دیر شد! دنده {p.gear + 1}",
                "top": "🚀 دنده آخر", "wait": ""}[state]
        lines += [
            f"{medal} <b>{esc(short(p.name, 14))}</b> · {int(p.distance)}m",
            track_bar(p.distance, race.distance, p.slot),
            f"⚙️ دنده {p.gear} · ⚡ {int(p.speed(now))} km/h · {stats}",
            rpm_bar(p),
            *([hint] if hint else []),
            "",
        ]
    events = [t for t in (event_text(race, e) for e in race.recent_events(now)) if t]
    if events:
        lines += [LINE, *events]
    return "\n".join(lines).rstrip()


def race_kb(race: Race, now: float) -> Markup:
    """برای هر بازیکن یک دکمهٔ مخصوص خودش (به ترتیب ورود، تا جای دکمه‌ها عوض نشود)."""
    buttons = []
    for p in race.players.values():
        name = short(p.name, 8)
        state = "done" if p.finish_position is not None else p.shift_state(now)
        label = {
            "done": f"🏁 {name}",
            "top": f"🚀 {name}",
            "wait": f"🔵 {name} · {p.gear}",
            "green": f"🟢 {name} ⚡ دنده {p.gear + 1}",
            "late": f"🔴 {name} ⚡ دنده {p.gear + 1}",
        }[state]
        buttons.append(Btn(label, callback_data=f"sh:{race.race_id}:{p.user_id}"))
    return Markup([buttons[i:i + 2] for i in range(0, len(buttons), 2)])


def render_result(race: Race, rewards: dict[int, int]) -> str:
    ranking = race.ranking()
    lines = ["🏁 <b>مسابقه تمام شد!</b>", ""]
    for p in ranking:
        lines.append(f"{MEDALS[p.finish_position - 1]} {esc(p.name)}")
    lines += ["", LINE, "", "🏆 برنده:", f"<b>{esc(ranking[0].name)}</b>", "",
              "⚡ تعویض‌های عالی:"]
    lines += [f"{esc(p.name)}: {p.perfect_shifts}" for p in sorted(ranking, key=lambda q: -q.perfect_shifts)[:5]]
    lines += ["", LINE, "", "💰 <b>جایزه‌ها</b>", ""]
    lines += [f"{esc(p.name)} +{fmt(rewards[p.user_id])} سکه" for p in ranking]
    return "\n".join(lines)


# ── چت خصوصی ──
GUIDE_PAGES = [
    "🏁 <b>مسابقه چیست؟</b>\n\n"
    "مسابقه بین اعضای یک گروه تلگرامی انجام می‌شود. هر کس یک ماشین دارد و هدف این است که "
    "زودتر از بقیه برسی و رتبهٔ بهتری بگیری. 🏆",

    "🚦 <b>شروع مسابقه</b>\n\nداخل گروه بنویس:\n<code>/race</code>\n\n"
    "بعد روی «🏎️ ساخت مسابقه» بزن و تعداد بازیکن‌ها را انتخاب کن. بقیه با دکمهٔ «🏎️ پیوستن» "
    "وارد می‌شوند. وقتی همه آماده بودند، سازندهٔ مسابقه «🚦 شروع مسابقه» را می‌زند. "
    "بعد از شمارش 3، 2، 1 مسابقه شروع می‌شود!",

    "⚙️ <b>دنده (Gear) چیست؟</b>\n\n"
    "ماشین تو ۶ دنده دارد: 1 ← 2 ← 3 ← 4 ← 5 ← 6\n"
    "هر چه سریع‌تر بروی، باید دنده را بالاتر ببری تا ماشین سریع‌تر شود.\n\n"
    "روی نوار RPM یک نقطهٔ ⚪ حرکت می‌کند:\n"
    "🟦 پایین (هنوز زوده)\n🟩 لحظهٔ طلایی تعویض\n🟥 بیش از حد (دیر شده)",

    "🟢 <b>تعویض عالی</b>\n\n"
    "وقتی نقطه به 🟩 رسید، دکمهٔ اسم تو سبز می‌شود و بازی می‌نویسد:\n"
    "🟢 الان! ⚡ دنده 4\n\n"
    "سریع روی دکمهٔ خودت بزن! (فقط دکمهٔ خودت کار می‌کند)\n"
    "اگر توی محدودهٔ سبز بزنی می‌گوید 🔥 تعویض عالی! و سرعت و شتابت بیشتر می‌شود.",

    "⚠️ <b>تعویض از دست رفته</b>\n\n"
    "اگر حدود ۲ ثانیه از محدودهٔ سبز رد شوی و دنده نزنی، ماشین خودش دنده را عوض می‌کند و می‌نویسد:\n"
    "⚠️ تعویض را از دست دادی\n\n"
    "در این حالت سرعتت حفظ می‌شود یا خیلی کم کم می‌شود. نگران نباش، یک اشتباه مسابقه را خراب نمی‌کند!\n"
    "اگر زودتر از سبز شدن بزنی، اتفاقی نمی‌افتد.",

    "🏆 <b>رتبه‌بندی</b>\n\n"
    "رتبهٔ اول، دوم، سوم و ... بر اساس ترتیب رسیدن به خط پایان تعیین می‌شود.\n\n"
    "اما برای اینکه مسابقه طولانی نشود: وقتی فقط یک نفر باقی بماند، رتبهٔ آخر خودکار به او داده می‌شود "
    "و مسابقه همان لحظه تمام می‌شود. پس آخری لازم نیست تا خط پایان برود!",

    "💰 <b>سکه، ارتقا و سطح</b>\n\n"
    "💰 بعد از هر مسابقه، بر اساس رتبه‌ات سکه می‌گیری.\n"
    "🏎️ با سکه‌ها ماشینت را ارتقا بده: موتور، لاستیک، گیربکس، اگزوز، آیرو و ECU.\n"
    "📈 هر بخش از سطح 1 شروع می‌شود و تا سطح 50 می‌رود. ارتقاها هر چه بالاتر بروی گران‌تر می‌شوند.\n\n"
    "برای ارتقا در همین چت روی «🏎️ ماشین من» بزن.",

    "💡 <b>نکتهٔ مهم</b>\n\n"
    "بازی خیلی ساده است:\n"
    "1️⃣ در گروه /race بزن و به مسابقه بپیوند\n"
    "2️⃣ نوار RPM خودت را نگاه کن\n"
    "3️⃣ وقتی 🟢 شد، سریع دکمهٔ خودت را بزن\n"
    "4️⃣ سبقت بگیر و به خط پایان برس\n"
    "5️⃣ سکه بگیر و ماشینت را ارتقا بده\n"
    "6️⃣ دوباره مسابقه بده! 🔁",
]


def guide_page(i: int) -> tuple[str, Markup]:
    i = max(0, min(i, len(GUIDE_PAGES) - 1))
    nav = []
    if i > 0:
        nav.append(Btn("◀️ قبلی", callback_data=f"gd:{i - 1}"))
    nav.append(Btn(f"{i + 1}/{len(GUIDE_PAGES)}", callback_data="noop"))
    if i < len(GUIDE_PAGES) - 1:
        nav.append(Btn("بعدی ▶️", callback_data=f"gd:{i + 1}"))
    return f"📖 <b>راهنمای بازی</b>\n\n{GUIDE_PAGES[i]}", Markup([nav])


def render_car(pl: dict) -> tuple[str, Markup]:
    lines = ["🏎️ <b>ماشین شما</b>", "", f"💰 سکه‌ها: {fmt(pl['coins'])}", ""]
    rows = []
    for key, u in C.UPGRADES.items():
        lvl = pl[key]
        lines.append(f"{u['emoji']} {u['label']} Lv.{lvl} — {u['desc']}")
        if lvl >= C.MAX_LEVEL:
            label = f"{u['emoji']} {u['label']} ✅ حداکثر سطح"
        else:
            label = f"{u['emoji']} ارتقای {u['label']} · {fmt(C.upgrade_cost(key, lvl))} 💰"
        rows.append([Btn(label, callback_data=f"up:{key}")])
    lines += ["", "با سکه‌هایی که از مسابقه می‌گیری، ماشینت را ارتقا بده. 👇"]
    return "\n".join(lines), Markup(rows)


def render_balance(pl: dict) -> str:
    best = pl["best_position"] if pl["best_position"] else "-"
    return (f"💰 <b>موجودی شما</b>\n\n🪙 سکه‌ها: {fmt(pl['coins'])}\n\n📊 <b>آمار</b>\n"
            f"🏁 تعداد مسابقه‌ها: {pl['total_races']}\n🏆 بردها: {pl['wins']}\n"
            f"🥉 روی سکو (سه نفر اول): {pl['podiums']}\n⭐ بهترین رتبه: {best}")


# ═══════════════════════════ کمکی‌های ارسال/ویرایش ═══════════════════════════
async def safe_edit(bot, chat_id: int, message_id: int, text: str, markup: Markup | None = None,
                    retries: int = 0) -> str:
    """ویرایش امن پیام. خروجی: 'ok' | 'gone' (پیام حذف شده) | 'fail'."""
    for attempt in range(retries + 1):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                        reply_markup=markup, parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True)
            return "ok"
        except RetryAfter as e:
            wait = e.retry_after.total_seconds() if hasattr(e.retry_after, "total_seconds") else float(e.retry_after)
            log.warning("flood control: retry after %.1fs", wait)
            if attempt < retries:
                await asyncio.sleep(min(wait, 10) + 0.2)
        except BadRequest as e:
            msg = str(e).lower()
            if "not modified" in msg:
                return "ok"
            if "not found" in msg or "can't be edited" in msg or "message to edit" in msg:
                return "gone"
            log.warning("edit failed: %s", e)
            return "fail"
        except TelegramError as e:
            log.warning("edit error: %s", e)
    return "fail"


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return task


def cleanup(race: Race) -> None:
    RACES.pop(race.race_id, None)
    if CHAT_RACE.get(race.chat_id) == race.race_id:
        CHAT_RACE.pop(race.chat_id, None)


# ═══════════════════════════ ۲) هندلرهای گروه ═══════════════════════════
async def cmd_race(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, msg = update.effective_chat, update.effective_user, update.effective_message
    if chat is None or user is None or msg is None:
        return
    if chat.type == ChatType.PRIVATE:
        await msg.reply_text(PRIVATE_RACE)
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if chat.id in CHAT_RACE:
        await msg.reply_text("⚠️ در این گروه یک مسابقهٔ فعال وجود دارد. اول آن را تمام کنید.")
        return
    DB.upsert_player(user.id, user.username, user.first_name)
    await msg.reply_text(panel_text(), reply_markup=panel_kb(user.id), parse_mode=ParseMode.HTML)


async def on_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """پنل /race: rc:c:<uid> | rc:x:<uid> | rc:n:<uid>:<n>"""
    q = update.callback_query
    parts = q.data.split(":")
    action, owner = parts[1], int(parts[2])
    if q.from_user.id != owner:
        await q.answer(NOT_YOUR_PANEL, show_alert=True)
        return
    chat_id, mid = q.message.chat_id, q.message.message_id
    if action == "x":
        await q.answer()
        await safe_edit(ctx.bot, chat_id, mid, "❌ ساخت مسابقه لغو شد.")
        return
    if action == "c":
        await q.answer()
        rows = _number_rows(f"rc:n:{owner}")
        await safe_edit(ctx.bot, chat_id, mid, capacity_text(), Markup(rows))
        return
    if action == "n":
        n = int(parts[3])
        if not C.MIN_PLAYERS <= n <= C.MAX_PLAYERS:
            await q.answer("❌ تعداد نامعتبر است.", show_alert=True)
            return
        if chat_id in CHAT_RACE:
            await q.answer("⚠️ در این گروه یک مسابقهٔ فعال وجود دارد.", show_alert=True)
            return
        user = q.from_user
        DB.upsert_player(user.id, user.username, user.first_name)
        race_id = DB.create_race(chat_id, owner, n, C.RACE_DISTANCE, mid)
        race = Race(race_id, chat_id, owner, n)
        race.message_id = mid
        race.add_player(owner, user_name(user), DB.get_levels(owner))
        RACES[race_id] = race
        CHAT_RACE[chat_id] = race_id
        DB.sync_players(race)
        await q.answer()
        await safe_edit(ctx.bot, chat_id, mid, render_lobby(race), lobby_kb(race), retries=2)
        spawn(lobby_expiry(ctx.bot, race))


async def lobby_expiry(bot, race: Race) -> None:
    """لابی‌ای که شروع نشود بعد از مدتی خودکار لغو می‌شود تا گروه قفل نماند."""
    await asyncio.sleep(C.LOBBY_TIMEOUT)
    if race.phase == Phase.LOBBY and RACES.get(race.race_id) is race:
        race.cancel()
        DB.set_race_status(race.race_id, "cancelled")
        cleanup(race)
        await safe_edit(bot, race.chat_id, race.message_id, "⌛ مسابقه به دلیل عدم فعالیت لغو شد.")


async def on_lobby(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """لابی: lb:<action>:<race_id>[:<n>]"""
    q = update.callback_query
    parts = q.data.split(":")
    action, race = parts[1], RACES.get(int(parts[2]))
    user = q.from_user
    if race is None or race.phase in (Phase.CANCELLED, Phase.FINISHED):
        await q.answer("⌛ این مسابقه دیگر فعال نیست.", show_alert=True)
        return
    if race.phase != Phase.LOBBY:
        await q.answer("🔒 مسابقه شروع شده و لابی قفل است.", show_alert=True)
        return
    bot, chat_id, mid = ctx.bot, race.chat_id, race.message_id
    is_host = user.id == race.host_id

    async def show_lobby() -> None:
        await safe_edit(bot, chat_id, mid, render_lobby(race), lobby_kb(race), retries=2)

    if action in ("j", "l") and race.confirming:
        await q.answer("⏳ سازنده در حال تأیید شروع مسابقه است.", show_alert=True)
        return

    if action == "j":
        if user.id in race.players:
            await q.answer("✅ شما قبلاً به مسابقه پیوسته‌اید.")
            return
        if race.is_full:
            await q.answer("❌ ظرفیت مسابقه تکمیل شده است.", show_alert=True)
            return
        DB.upsert_player(user.id, user.username, user.first_name)
        race.add_player(user.id, user_name(user), DB.get_levels(user.id))
        DB.sync_players(race)
        await q.answer("✅ به مسابقه پیوستی!")
        await show_lobby()
        return

    if action == "l":
        if user.id not in race.players:
            await q.answer("❌ شما در این مسابقه نیستید.", show_alert=True)
            return
        if is_host:
            await q.answer("❌ سازنده نمی‌تواند خارج شود. از «تنظیمات مسابقه» می‌توانی مسابقه را لغو کنی.",
                           show_alert=True)
            return
        race.remove_player(user.id)
        DB.sync_players(race)
        await q.answer("🚪 از مسابقه خارج شدی.")
        await show_lobby()
        return

    # ── از اینجا به بعد فقط سازنده ──
    if not is_host:
        await q.answer(HOST_ONLY, show_alert=True)
        return

    if action == "s":
        race.confirming = False
        await q.answer()
        await safe_edit(bot, chat_id, mid, render_settings(race), settings_kb(race))
    elif action == "b":
        race.confirming = False
        await q.answer()
        await show_lobby()
    elif action == "c":
        err = race.set_capacity(int(parts[3]))
        if err == "below_current":
            await q.answer("❌ ظرفیت نمی‌تواند کمتر از تعداد بازیکنان فعلی باشد.", show_alert=True)
            return
        if err:
            await q.answer("❌ ظرفیت نامعتبر است.", show_alert=True)
            return
        DB.sync_players(race)
        await q.answer(f"✅ ظرفیت روی {race.max_players} تنظیم شد.")
        await show_lobby()
    elif action == "k":
        race.cancel()
        DB.set_race_status(race.race_id, "cancelled")
        cleanup(race)
        await q.answer()
        await safe_edit(bot, chat_id, mid, "❌ مسابقه توسط سازنده لغو شد.")
    elif action == "g":
        if len(race.players) < C.MIN_PLAYERS:
            await q.answer(f"❌ حداقل {C.MIN_PLAYERS} بازیکن برای شروع لازم است.", show_alert=True)
            return
        race.confirming = True
        await q.answer()
        await safe_edit(bot, chat_id, mid, render_confirm(race), confirm_kb(race))
    elif action == "y":
        if not race.confirming:
            await q.answer()
            return
        if len(race.players) < C.MIN_PLAYERS:
            race.confirming = False
            await q.answer(f"❌ حداقل {C.MIN_PLAYERS} بازیکن برای شروع لازم است.", show_alert=True)
            return
        race.phase = Phase.COUNTDOWN     # لابی قفل می‌شود؛ ورود/خروج/تغییر ظرفیت دیگر ممکن نیست
        race.confirming = False
        DB.sync_players(race)
        await q.answer("🚦 مسابقه شروع می‌شود!")
        spawn(run_race(ctx.application, race))


async def on_shift(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """دکمهٔ تعویض دنده: sh:<race_id>:<user_id>"""
    q = update.callback_query
    _, rid, uid = q.data.split(":")
    race, uid = RACES.get(int(rid)), int(uid)
    if q.from_user.id != uid:
        await q.answer(NOT_YOUR_CONTROL, show_alert=True)
        return
    if race is None or race.phase != Phase.RUNNING:
        await q.answer("🏁 این مسابقه تمام شده است.")
        return
    gear_before = race.players[uid].gear if uid in race.players else 0
    result = race.press_shift(uid, time.monotonic())
    texts = {
        ShiftResult.PERFECT: f"🔥 تعویض عالی! دنده {gear_before + 1}",
        ShiftResult.LATE: f"🟠 دیر شد! دنده {gear_before + 1} (بدون پاداش)",
        ShiftResult.EARLY: "⏳ هنوز زوده! صبر کن تا 🟩 شود",
        ShiftResult.TOP_GEAR: "🚀 در دندهٔ آخر هستی!",
        ShiftResult.INACTIVE: "🏁 تو مسابقه را تمام کرده‌ای.",
    }
    await q.answer(texts[result])


# ═══════════════════════════ ۳) اجرای مسابقه ═══════════════════════════
async def run_race(app: Application, race: Race) -> None:
    bot, chat_id, mid = app.bot, race.chat_id, race.message_id
    try:
        for p in race.players.values():          # آپگریدهای لحظهٔ شروع اعمال می‌شود
            p.car = CarStats.from_levels(DB.get_levels(p.user_id))
        DB.set_race_status(race.race_id, "countdown")

        for label in ("3...", "2...", "1..."):
            if await safe_edit(bot, chat_id, mid, f"🏁 <b>مسابقه شروع می‌شود</b>\n\n{label}") == "gone":
                return await abort_race(race)
            await asyncio.sleep(C.COUNTDOWN_STEP)

        race.start(time.monotonic())
        DB.set_race_status(race.race_id, "running")
        now = time.monotonic()
        if await safe_edit(bot, chat_id, mid, render_race(race, now), race_kb(race, now)) == "gone":
            return await abort_race(race)

        next_tick = time.monotonic() + C.TICK_SECONDS
        while race.phase == Phase.RUNNING:
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
            now = time.monotonic()
            next_tick = max(next_tick + C.TICK_SECONDS, now + C.TICK_SECONDS / 2)
            race.advance(now)
            race.update_positions(now)
            DB.save_state(race, now)
            if race.phase != Phase.RUNNING:
                break
            if await safe_edit(bot, chat_id, mid, render_race(race, now), race_kb(race, now)) == "gone":
                return await abort_race(race)
        await finish_race(app, race)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("race %s crashed", race.race_id)
        race.cancel()
        DB.set_race_status(race.race_id, "aborted")
        await safe_edit(bot, chat_id, mid, "⚠️ خطایی رخ داد و مسابقه لغو شد.")
    finally:
        cleanup(race)


async def abort_race(race: Race) -> None:
    log.warning("race %s aborted: message is gone", race.race_id)
    race.cancel()
    DB.set_race_status(race.race_id, "aborted")


async def finish_race(app: Application, race: Race) -> None:
    now = time.monotonic()
    table = C.REWARDS[len(race.players)]
    rewards = {p.user_id: table[p.finish_position - 1] for p in race.players.values()}
    DB.finish_race(race, rewards, now)
    kb = None
    try:
        kb = Markup([[Btn("🏎️ ارتقای ماشین", url=f"https://t.me/{app.bot.username}?start=car")]])
    except Exception:  # noqa: BLE001
        pass
    await safe_edit(app.bot, race.chat_id, race.message_id, render_result(race, rewards), kb, retries=3)


# ═══════════════════════════ ۴) چت خصوصی ═══════════════════════════
def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == ChatType.PRIVATE)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user, msg = update.effective_user, update.effective_message
    if user is None or msg is None:
        return
    if not _is_private(update):
        await msg.reply_text("🏁 برای ساخت مسابقه در همین گروه /race را بزنید.\nراهنمای بازی: چت خصوصی ربات.")
        return
    is_new = DB.upsert_player(user.id, user.username, user.first_name)
    if ctx.args and ctx.args[0] == "car":
        await show_car(update)
        return
    await msg.reply_text(WELCOME if is_new else WELCOME_BACK, reply_markup=MAIN_KEYBOARD, parse_mode=ParseMode.HTML)


async def show_car(update: Update) -> None:
    user = update.effective_user
    DB.upsert_player(user.id, user.username, user.first_name)
    text, kb = render_car(DB.get_player(user.id))
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def on_guide_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text, kb = guide_page(0)
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def on_car_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await show_car(update)


async def on_balance_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    DB.upsert_player(user.id, user.username, user.first_name)
    await update.effective_message.reply_text(render_balance(DB.get_player(user.id)), parse_mode=ParseMode.HTML)


async def on_private_other(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("از منوی پایین یکی را انتخاب کن 👇", reply_markup=MAIN_KEYBOARD)


async def on_guide_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    text, kb = guide_page(int(q.data.split(":")[1]))
    await safe_edit(ctx.bot, q.message.chat_id, q.message.message_id, text, kb)


async def on_upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    key = q.data.split(":")[1]
    status, val = DB.buy_upgrade(q.from_user.id, key)
    if status == "poor":
        await q.answer(f"❌ سکهٔ کافی نداری! هزینه: {fmt(val)} سکه", show_alert=True)
        return
    if status == "max":
        await q.answer("✅ این بخش به حداکثر سطح رسیده است.", show_alert=True)
        return
    if status != "ok":
        await q.answer("❌ اول /start را در چت خصوصی بزن.", show_alert=True)
        return
    u = C.UPGRADES[key]
    await q.answer(f"✅ {u['label']} به سطح {val} رسید!")
    text, kb = render_car(DB.get_player(q.from_user.id))
    await safe_edit(ctx.bot, q.message.chat_id, q.message.message_id, text, kb)


async def on_noop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("unhandled error", exc_info=ctx.error)
    if isinstance(update, Update) and update.callback_query:
        try:
            await update.callback_query.answer("⚠️ خطایی رخ داد. دوباره تلاش کن.")
        except TelegramError:
            pass


# ═══════════════════════════ اجرا ═══════════════════════════
async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("race", "ساخت مسابقه (فقط در گروه)"),
        BotCommand("start", "شروع و منوی اصلی"),
    ])


def build_app() -> Application:
    app = ApplicationBuilder().token(C.BOT_TOKEN).concurrent_updates(True).post_init(post_init).build()
    exact = lambda text: filters.ChatType.PRIVATE & filters.Regex(f"^{re.escape(text)}$")  # noqa: E731

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("race", cmd_race))
    app.add_handler(CallbackQueryHandler(on_panel, pattern=r"^rc:"))
    app.add_handler(CallbackQueryHandler(on_lobby, pattern=r"^lb:"))
    app.add_handler(CallbackQueryHandler(on_shift, pattern=r"^sh:"))
    app.add_handler(CallbackQueryHandler(on_guide_cb, pattern=r"^gd:"))
    app.add_handler(CallbackQueryHandler(on_upgrade, pattern=r"^up:"))
    app.add_handler(CallbackQueryHandler(on_noop, pattern=r"^noop$"))
    app.add_handler(MessageHandler(exact(BTN_GUIDE), on_guide_btn))
    app.add_handler(MessageHandler(exact(BTN_CAR), on_car_btn))
    app.add_handler(MessageHandler(exact(BTN_BALANCE), on_balance_btn))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_other))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    global DB
    logging.basicConfig(level=C.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not C.BOT_TOKEN:
        raise SystemExit("❌ متغیر BOT_TOKEN تنظیم نشده است. فایل .env را بسازید (نمونه: .env.example).")
    DB = Database(C.DB_PATH)
    aborted = DB.abort_unfinished()
    if aborted:
        log.warning("%d unfinished race(s) from previous run were aborted", aborted)
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
