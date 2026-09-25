"""رنگی کردن تمام دکمه‌های شیشه‌ای (Inline Button) ربات.

تلگرام (Bot API 9.4 به بعد) سه رنگ برای دکمه‌ها دارد:
    "success" = سبز | "danger" = قرمز | "primary" = آبی
(کلاینت‌های قدیمی‌تر از ۹ فوریهٔ ۲۰۲۶ دکمه را بدون رنگ نشان می‌دهند؛ مشکلی پیش نمی‌آید.)

روش استفاده: در bot.py به جای InlineKeyboardButton از `styled_button` استفاده می‌شود.
رنگ هر دکمه از روی callback_data و متنش تعیین می‌شود؛ برای تغییر رنگ‌ها فقط همین فایل را ویرایش کنید.
اگر بخواهید موقتاً رنگ‌ها خاموش شوند، در .env بنویسید: BUTTON_COLORS=0
"""
from __future__ import annotations

import os

from telegram import InlineKeyboardButton

SUCCESS, DANGER, PRIMARY = "success", "danger", "primary"

ENABLED = os.getenv("BUTTON_COLORS", "1") != "0"

# رنگ بر اساس ابتدای callback_data (اولین مورد منطبق برنده است)
PREFIX_STYLES: list[tuple[str, str]] = [
    ("rc:c:", SUCCESS),   # 🏎️ ساخت مسابقه
    ("rc:x:", DANGER),    # ❌ لغو (پنل ساخت)
    ("rc:n:", PRIMARY),   # انتخاب تعداد بازیکن
    ("lb:j:", SUCCESS),   # 🏎️ پیوستن
    ("lb:l:", DANGER),    # 🚪 خروج
    ("lb:s:", PRIMARY),   # ⚙️ تنظیمات مسابقه
    ("lb:g:", SUCCESS),   # 🚦 شروع مسابقه
    ("lb:y:", SUCCESS),   # ✅ شروع (تأیید)
    ("lb:k:", DANGER),    # 🗑️ لغو مسابقه
    ("lb:c:", PRIMARY),   # تغییر ظرفیت
    ("lb:b:", PRIMARY),   # 🔙 بازگشت  (اگر متن ❌ داشته باشد قرمز می‌شود)
    ("gd:", PRIMARY),     # ◀️ ▶️ صفحات راهنما
    ("up:", PRIMARY),     # ارتقای ماشین (سطح آخر: سبز)
    ("noop", PRIMARY),
]

# دکمه‌های کنترل دنده در مسابقه (sh:) از روی ایموجی ابتدای متن رنگ می‌گیرند
SHIFT_EMOJI_STYLES = {
    "🟢": SUCCESS,   # الان تعویض کن!
    "🔴": DANGER,    # دیر شد
    "🔵": PRIMARY,   # هنوز زوده
    "🚀": PRIMARY,   # دنده آخر
    "🏁": SUCCESS,   # مسابقه را تمام کرده
}


def style_for(text: str, callback_data: str | None = None, url: str | None = None) -> str | None:
    """رنگ مناسب یک دکمه را برمی‌گرداند."""
    if url:
        return PRIMARY
    data = callback_data or ""
    if data.startswith("sh:"):
        return SHIFT_EMOJI_STYLES.get(text[:1], PRIMARY)
    if "❌" in text:
        return DANGER
    if "✅" in text:
        return SUCCESS
    for prefix, style in PREFIX_STYLES:
        if data.startswith(prefix):
            return style
    return PRIMARY  # هر دکمهٔ ناشناخته هم آبی می‌شود تا همه رنگی باشند


def styled_button(text: str, callback_data: str | None = None, url: str | None = None, **kwargs) -> InlineKeyboardButton:
    """جایگزین InlineKeyboardButton: همان آرگومان‌ها + رنگ خودکار."""
    style = style_for(text, callback_data, url) if ENABLED else None
    if style is None:
        return InlineKeyboardButton(text, callback_data=callback_data, url=url, **kwargs)
    try:
        return InlineKeyboardButton(text, callback_data=callback_data, url=url, style=style, **kwargs)
    except TypeError:
        # نسخهٔ قدیمی python-telegram-bot که پارامتر style ندارد: مستقیم به API می‌فرستیم
        api_kwargs = dict(kwargs.pop("api_kwargs", None) or {}, style=style)
        return InlineKeyboardButton(text, callback_data=callback_data, url=url, api_kwargs=api_kwargs, **kwargs)
