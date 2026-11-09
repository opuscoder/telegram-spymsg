import asyncio
import html
import os
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BusinessMessagesDeleted, Message
from dotenv import load_dotenv

from storage import State, Storage

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "messages.db")
STATE_PATH = os.environ.get("STATE_PATH", "state.json")
OWNER_ID_ENV = os.environ.get("OWNER_ID", "").strip()

storage = Storage(DB_PATH)
state = State(STATE_PATH)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def get_owner_id() -> int | None:
    if OWNER_ID_ENV:
        return int(OWNER_ID_ENV)
    return state.get_owner_id()


def sender_display_name(user) -> str:
    if user is None:
        return "Unknown"
    name = " ".join(filter(None, [user.first_name, user.last_name]))
    if user.username:
        return f"{name} (@{user.username})".strip()
    return name or f"id{user.id}"


def media_info(message: Message):
    """Возвращает (media_type, file_id) для первого найденного медиа."""
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.video:
        return "video", message.video.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.document:
        return "document", message.document.file_id
    if message.sticker:
        return "sticker", message.sticker.file_id
    if message.animation:
        return "animation", message.animation.file_id
    if message.video_note:
        return "video_note", message.video_note.file_id
    return None, None


@dp.message(Command("start"))
async def on_start(message: Message):
    configured_owner = get_owner_id()
    if configured_owner is None:
        state.set_owner_id(message.from_user.id)
        await message.answer(
            "Готово! Я запомнил ваш id "
            f"(<code>{message.from_user.id}</code>) и буду присылать сюда "
            "уведомления об удалённых сообщениях.\n\n"
            "Теперь подключите меня как бота для Telegram Business:\n"
            "Настройки → Telegram Business → Чат-боты → выбрать этого бота, "
            "включить права «Читать сообщения»."
        )
    elif configured_owner == message.from_user.id:
        await message.answer("Уже подключено, жду уведомлений об удалении.")
    else:
        await message.answer(
            "Этот бот уже привязан к другому владельцу."
        )


@dp.message(Command("status"))
async def on_status(message: Message):
    owner = get_owner_id()
    await message.answer(
        f"owner_id: <code>{owner}</code>\n"
        f"ваш id: <code>{message.from_user.id}</code>"
    )


@dp.business_message()
async def on_business_message(message: Message):
    media_type, file_id = media_info(message)
    storage.save_message(
        business_connection_id=message.business_connection_id,
        chat_id=message.chat.id,
        msg_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_display_name(message.from_user),
        text=message.text or message.caption or "",
        media_type=media_type,
        file_id=file_id,
        date_sent=int(message.date.timestamp()) if message.date else None,
    )


@dp.edited_business_message()
async def on_business_message_edited(message: Message):
    media_type, file_id = media_info(message)
    storage.save_message(
        business_connection_id=message.business_connection_id,
        chat_id=message.chat.id,
        msg_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_display_name(message.from_user),
        text=message.text or message.caption or "",
        media_type=media_type,
        file_id=file_id,
        date_sent=int(message.date.timestamp()) if message.date else None,
    )


@dp.deleted_business_messages()
async def on_business_messages_deleted(event: BusinessMessagesDeleted):
    owner_id = get_owner_id()
    if owner_id is None:
        print("[main] owner_id не задан — напишите боту /start")
        return

    chat_title = (
        event.chat.title
        or event.chat.username
        or " ".join(filter(None, [event.chat.first_name, event.chat.last_name]))
        or str(event.chat.id)
    )

    for msg_id in event.message_ids:
        record = storage.get_message(
            event.business_connection_id, event.chat.id, msg_id
        )
        if record is None:
            continue

        storage.mark_deleted(event.business_connection_id, event.chat.id, msg_id)

        when = (
            datetime.fromtimestamp(record["date_sent"], tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
            if record["date_sent"]
            else "?"
        )
        text_block = (
            html.escape(record["text"]) if record["text"] else "<i>(без текста)</i>"
        )
        caption = (
            f"🗑 <b>Удалено сообщение</b>\n"
            f"Чат: <b>{html.escape(chat_title)}</b>\n"
            f"От: {html.escape(record['sender_name'] or '')}\n"
            f"Отправлено: {when}\n\n"
            f"{text_block}"
        )

        try:
            if record["file_id"]:
                await send_media(owner_id, record["media_type"], record["file_id"], caption)
            else:
                await bot.send_message(owner_id, caption)
        except Exception as e:
            print(f"[main] не удалось отправить уведомление: {e}")


async def send_media(chat_id: int, media_type: str, file_id: str, caption: str):
    senders = {
        "photo": bot.send_photo,
        "video": bot.send_video,
        "voice": bot.send_voice,
        "audio": bot.send_audio,
        "document": bot.send_document,
        "sticker": lambda chat_id, **kw: bot.send_sticker(chat_id, file_id),
        "animation": bot.send_animation,
        "video_note": lambda chat_id, **kw: bot.send_video_note(chat_id, file_id),
    }
    send = senders.get(media_type)
    if send is None:
        await bot.send_message(chat_id, caption)
        return
    if media_type in ("sticker", "video_note"):
        await send(chat_id)
        await bot.send_message(chat_id, caption)
    else:
        await send(chat_id, file_id, caption=caption)


async def main():
    me = await bot.get_me()
    print(f"[main] бот запущен: @{me.username}")
    print(f"[main] owner_id: {get_owner_id()}")
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "business_connection",
            "business_message",
            "edited_business_message",
            "deleted_business_messages",
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
