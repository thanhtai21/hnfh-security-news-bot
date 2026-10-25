# -*- coding: utf-8 -*-
"""
discord_check.py - Công cụ CHẨN ĐOÁN quyền Discord (KHÔNG đăng bài, KHÔNG gọi AI).

Kết nối bằng DISCORD_BOT_TOKEN, rồi in ra:
  - bot đang ở server nào, có thấy kênh mục tiêu không,
  - quyền thực tế của bot trên kênh (Tạo bài đăng, Gửi tin trong bài...),
  - các "permission overwrite" (ghi đè quyền) của KÊNH và DANH MỤC chứa kênh.
Không ghi token ra log.
"""
import asyncio
import logging
import os
import sys

import discord

from config import DISCORD_CHANNEL_ID
from discord_poster import _log_forbidden_diagnostics

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)-7s] %(name)s %(message)s")
log = logging.getLogger("check")

WATCH = [
    ("view_channel", "Xem kênh"),
    ("send_messages", "Tạo bài đăng"),
    ("send_messages_in_threads", "Gửi tin trong bài"),
    ("create_public_threads", "Tạo thread công khai"),
    ("embed_links", "Nhúng liên kết"),
    ("read_message_history", "Đọc lịch sử"),
]


def dump_overwrites(channel, label, bot_user_id):
    """In ghi đè quyền (chỉ các quyền liên quan) của một kênh/danh mục."""
    overwrites = getattr(channel, "overwrites", {}) or {}
    log.info("[CHECK] %s '%s': %d mục ghi đè quyền.", label, channel.name,
             len(overwrites))
    for target, overwrite in overwrites.items():
        allow, deny = overwrite.pair()
        allowed = [lbl for attr, lbl in WATCH if getattr(allow, attr, False)]
        denied = [lbl for attr, lbl in WATCH if getattr(deny, attr, False)]
        kind = "Member" if isinstance(target, discord.Member) else "Role"
        is_bot = (getattr(target, "id", None) == bot_user_id)
        log.info("[CHECK]   %s %s%s | CHO PHÉP: %s | CẤM: %s",
                 kind, getattr(target, "name", "?"),
                 " (CHÍNH BOT)" if is_bot else "",
                 ", ".join(allowed) or "-", ", ".join(denied) or "-")


async def main():
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        log.error("Thiếu biến môi trường DISCORD_BOT_TOKEN.")
        sys.exit(1)

    client = discord.Client(intents=discord.Intents.default())

    @client.event
    async def on_ready():
        try:
            log.info("[CHECK] Đăng nhập: %s (id=%s)", client.user,
                     client.user.id)
            _log_forbidden_diagnostics(client, DISCORD_CHANNEL_ID)
            for guild in client.guilds:
                channel = guild.get_channel(DISCORD_CHANNEL_ID)
                if channel is None:
                    continue
                me = guild.me
                roles = [r.name for r in getattr(me, "roles", [])]
                log.info("[CHECK] Role của bot: %s", ", ".join(roles) or "-")
                dump_overwrites(channel, "KÊNH", client.user.id)
                if channel.category is not None:
                    dump_overwrites(channel.category, "DANH MỤC", client.user.id)
        except Exception as exc:
            log.error("[CHECK] Lỗi khi chẩn đoán: %r", exc)
        finally:
            await client.close()

    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
