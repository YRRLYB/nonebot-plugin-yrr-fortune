"""YRR_运势 · NoneBot 每日运势卡片插件.

Commands
--------
运势 / 今日运势          生成或复读今天的运势卡片
运势模板 [1-6]           切换渲染模板；不带编号时发送模板总览
背景图 / 运势背景图       回复运势图获取原图，或随机取一张背景
重置运势                  群管/群主：清空所有人的今日运势
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
from PIL import Image, ImageOps
from nonebot import get_driver, get_plugin_config, on_command, on_regex, require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_localstore")
require("nonebot_plugin_htmlrender")

from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_htmlrender import html_to_pic
from nonebot_plugin_localstore import get_plugin_cache_dir, get_plugin_data_dir

from .config import Config, fortune_texts
from .core import (
    TEMPLATE_NAMES,
    TEMPLATE_VIEWPORTS,
    avatar_data_uri,
    background_data_uri,
    build_render_context,
    dedupe_texts,
    generate_fortune,
    is_fresh,
    normalize_user,
    split_fortune,
)

__plugin_meta__ = PluginMetadata(
    name="YRR_运势",
    description="每日运势卡片：七档运势、六维雷达、幸运色/数字/方位、宜忌与六套模板",
    usage=(
        "发送「运势」查看今日运势；"
        "发送「运势模板 1-6」切换模板；"
        "回复运势图发送「背景图」获取原图。"
    ),
    type="application",
    homepage="https://github.com/YRRLYB/nonebot-plugin-yrr-fortune",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------

plugin_config = get_plugin_config(Config)
TEXTS = dedupe_texts(fortune_texts)

PLUGIN_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PLUGIN_DIR / "templates"
if not TEMPLATE_DIR.is_dir():
    # The original in-tree plugin keeps 1.html ... 6.html next to __init__.py,
    # while the published wheel stores them under templates/.
    TEMPLATE_DIR = PLUGIN_DIR
HELP_IMAGE_PATH = PLUGIN_DIR / "help.jpg"


def _ensure_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(f"[YRR运势] 无法创建目录 {path}: {exc}")
    return path


def _resolve_storage() -> tuple[Path, Path]:
    """Use nonebot-plugin-localstore when possible, with a CWD fallback."""

    fallback_data = Path.cwd() / "data" / "YRR_database"
    fallback_cache = fallback_data / "cache"
    try:
        data_dir = get_plugin_data_dir()
        cache_dir = get_plugin_cache_dir()
    except Exception as exc:  # localstore rejects imports outside a plugin
        logger.warning(f"[YRR运势] localstore 不可用，回退到 {fallback_data}: {exc}")
        data_dir, cache_dir = fallback_data, fallback_cache
    return _ensure_dir(data_dir), _ensure_dir(cache_dir)


DATA_DIR, CACHE_DIR = _resolve_storage()
BG_CACHE_DIR = _ensure_dir(CACHE_DIR / "backgrounds")
AVATAR_CACHE_DIR = _ensure_dir(CACHE_DIR / "avatars")
USER_DATA_FILE = DATA_DIR / "user_data.json"
OLD_USER_DATA_FILE = Path.cwd() / "data" / "YRR_database" / "user_data.json"

BACKGROUND_IMAGE_URL = (
    os.getenv("FORTUNE_BACKGROUND_URL") or plugin_config.fortune_background_url
)
if os.getenv("BACKGROUND_IMAGE_URL") and not os.getenv("FORTUNE_BACKGROUND_URL"):
    logger.warning(
        "[YRR运势] BACKGROUND_IMAGE_URL 已弃用，改用 FORTUNE_BACKGROUND_URL；"
        "当前使用默认的 sort=mp 国内竖屏线路。"
    )
PRELOAD_COUNT = max(
    0, min(int(os.getenv("FORTUNE_PRELOAD_COUNT", plugin_config.fortune_preload_count)), 8)
)
HTTP_TIMEOUT = max(
    3.0, float(os.getenv("FORTUNE_HTTP_TIMEOUT", plugin_config.fortune_http_timeout))
)
INSECURE_SSL = os.getenv(
    "FORTUNE_INSECURE_SSL", str(plugin_config.fortune_insecure_ssl)
).strip().lower() in {"1", "true", "yes", "on"}
MAX_MESSAGE_MAP = max(
    50, int(os.getenv("FORTUNE_MAX_MESSAGE_MAP", plugin_config.fortune_max_message_map))
)

# A missing font is not fatal: Chromium falls back to the system sans-serif.
_FONT_CANDIDATES = [
    DATA_DIR / "fonts" / "msyh.ttc",
    Path.cwd() / "data" / "YRR_database" / "fonts" / "msyh.ttc",
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
]
FONT_FILE_PATH = next((p for p in _FONT_CANDIDATES if p.is_file()), None)

LOADED_TEMPLATES: Dict[str, str] = {}

# ---------------------------------------------------------------------------
# User data
# ---------------------------------------------------------------------------


def _default_user(uid: str) -> Dict[str, Any]:
    return normalize_user(
        {
            "qq_number": uid,
            "fortune": "?",
            "fortune_date": "",
            "fortune_text": "",
            "radar_data": [],
            "lucky_items": {},
            "template_choice": "1",
        },
        uid,
    )


class UserData:
    """Tiny JSON store with atomic writes, a backup copy and an asyncio lock."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_sync({"users": {}})

    # -- sync helpers (run in a worker thread) --------------------------------
    def _read_sync(self) -> Dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("user data root is not an object")
        data.setdefault("users", {})
        if not isinstance(data["users"], dict):
            data["users"] = {}
        return data

    def _write_sync(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        if self.path.exists():
            try:
                shutil.copy2(self.path, self.path.with_suffix(".bak"))
            except OSError:
                pass
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # -- async API -----------------------------------------------------------
    async def _load_unlocked(self) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(self._read_sync)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning(f"[YRR运势] user_data.json 读取失败，尝试备份: {exc}")
            backup = self.path.with_suffix(".bak")
            if backup.exists():
                try:
                    return await asyncio.to_thread(self._read_sync_path, backup)
                except Exception as backup_exc:
                    logger.warning(f"[YRR运势] 备份也读取失败: {backup_exc}")
            return {"users": {}}

    def _read_sync_path(self, path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("user data root is not an object")
        data.setdefault("users", {})
        return data

    async def load(self) -> Dict[str, Any]:
        async with self.lock:
            return await self._load_unlocked()

    async def get(self, uid: str) -> Optional[Dict[str, Any]]:
        data = await self.load()
        user = data["users"].get(uid)
        return dict(user) if isinstance(user, dict) else None

    async def mutate(self, uid: str, mutator) -> Dict[str, Any]:
        async with self.lock:
            data = await self._load_unlocked()
            users = data["users"]
            user = users.setdefault(uid, _default_user(uid))
            if not isinstance(user, dict):
                user = _default_user(uid)
                users[uid] = user
            result = mutator(user)
            if result is not False:
                await asyncio.to_thread(self._write_sync, data)
            return dict(user)

    async def replace(self, data: Dict[str, Any]) -> None:
        async with self.lock:
            data.setdefault("users", {})
            await asyncio.to_thread(self._write_sync, data)

    async def mutate_all(self, mutator) -> None:
        """Apply a change to every user under one lock (used by /重置运势)."""

        async with self.lock:
            data = await self._load_unlocked()
            mutator(data)
            await asyncio.to_thread(self._write_sync, data)


def _migrate_legacy_user_data() -> None:
    """Keep the old data/YRR_database/user_data.json when upgrading in place."""

    if USER_DATA_FILE.exists() or not OLD_USER_DATA_FILE.is_file():
        return
    if OLD_USER_DATA_FILE.resolve() == USER_DATA_FILE.resolve():
        return
    try:
        shutil.copy2(OLD_USER_DATA_FILE, USER_DATA_FILE)
        logger.info(f"[YRR运势] 已迁移旧用户数据: {OLD_USER_DATA_FILE} -> {USER_DATA_FILE}")
    except OSError as exc:
        logger.warning(f"[YRR运势] 旧用户数据迁移失败: {exc}")


_migrate_legacy_user_data()
USER_DATA = UserData(USER_DATA_FILE)

# ---------------------------------------------------------------------------
# Background / avatar cache
# ---------------------------------------------------------------------------

PRELOADED_PATHS: List[Path] = []
PRELOAD_LOCK = asyncio.Lock()
PRELOAD_TASK: Optional[asyncio.Task] = None
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(3)
IN_USE_BACKGROUNDS: Set[Path] = set()
FORTUNE_MSG_MAP: "OrderedDict[str, Path]" = OrderedDict()


def _is_image_bytes(data: bytes) -> bool:
    if len(data) < 1024:
        return False
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


def _background_filename() -> str:
    return f"{int(time.time() * 1000)}_{random.randrange(10000):04d}.jpg"


def _prepare_background(content: bytes, path: Path) -> bool:
    """Normalise the downloaded photo: EXIF rotate, downscale, re-encode JPEG.

    The upstream API often returns 4000x6000 photos (5-15 MB each).  Keeping
    four of those in memory and handing them to Chromium makes every card
    render needlessly slow, so cache a 1600px copy instead.
    """

    try:
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            image.thumbnail((1600, 2560), Image.Resampling.LANCZOS)
            image.save(path, "JPEG", quality=88, optimize=True)
        return path.is_file() and path.stat().st_size > 1024
    except Exception as exc:
        logger.debug(f"[YRR运势] 背景图处理失败: {exc}")
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


async def _fetch_image(url: str) -> Optional[bytes]:
    """GET an image, retrying once without TLS verification on a cert error."""

    attempts = (False,) if INSECURE_SSL else (True, False)
    for verify in attempts:
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
                verify=verify,
                headers={"User-Agent": "Mozilla/5.0 (YRR-Fortune/1.0)"},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            if verify:
                logger.debug(f"[YRR运势] TLS 校验失败，尝试不校验证书: {exc}")
                continue
            logger.debug(f"[YRR运势] 图片下载失败: {exc}")
        except Exception as exc:
            logger.debug(f"[YRR运势] 图片下载异常: {exc}")
    return None


async def _download_background(url: str) -> Optional[Path]:
    # Eight preloaded photos would otherwise open eight 5-15MB downloads at
    # once on startup; three in flight is enough to fill the pool quickly.
    async with DOWNLOAD_SEMAPHORE:
        content = await _fetch_image(url)
        if not content:
            return None
        path = BG_CACHE_DIR / _background_filename()
        if not await asyncio.to_thread(_prepare_background, content, path):
            return None
        return path


async def preload_images() -> None:
    if PRELOAD_COUNT <= 0:
        return
    async with PRELOAD_LOCK:
        PRELOADED_PATHS[:] = [p for p in PRELOADED_PATHS if p.exists()]
        needed = PRELOAD_COUNT - len(PRELOADED_PATHS)
        if needed <= 0:
            return
        results = await asyncio.gather(
            *(_download_background(BACKGROUND_IMAGE_URL) for _ in range(needed)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Path) and result.exists():
                PRELOADED_PATHS.append(result)


def schedule_preload() -> None:
    global PRELOAD_TASK
    if PRELOAD_TASK is None or PRELOAD_TASK.done():
        PRELOAD_TASK = asyncio.create_task(preload_images(), name="yrr-fortune-preload")


async def get_background_path() -> Optional[Path]:
    while PRELOADED_PATHS:
        path = PRELOADED_PATHS.pop(random.randrange(len(PRELOADED_PATHS)))
        if path.exists():
            IN_USE_BACKGROUNDS.add(path)
            schedule_preload()
            return path
    path = await _download_background(BACKGROUND_IMAGE_URL)
    if path:
        IN_USE_BACKGROUNDS.add(path)
        schedule_preload()
    return path


def release_background(path: Optional[Path]) -> None:
    if path is not None:
        IN_USE_BACKGROUNDS.discard(path)


async def clean_bg_cache() -> None:
    async with PRELOAD_LOCK:
        keep = set(PRELOADED_PATHS) | set(IN_USE_BACKGROUNDS) | set(FORTUNE_MSG_MAP.values())
        for path in BG_CACHE_DIR.glob("*"):
            if path.is_file() and path not in keep:
                try:
                    path.unlink()
                except OSError as exc:
                    logger.debug(f"[YRR运势] 清理背景缓存失败 {path}: {exc}")
        FORTUNE_MSG_MAP.clear()


def _avatar_cache_name(uid: str) -> str:
    safe = "".join(ch for ch in uid if ch.isalnum() or ch in "-_")[:64] or "unknown"
    if safe != uid:
        safe += "_" + str(abs(hash(uid)) % 100000)
    return f"{safe}.png"


async def get_avatar_path(uid: str) -> Optional[Path]:
    cache_file = AVATAR_CACHE_DIR / _avatar_cache_name(uid)
    if cache_file.exists():
        try:
            stat = await asyncio.to_thread(cache_file.stat)
            if (time.time() - stat.st_mtime) < 7 * 86400:
                return cache_file
        except OSError:
            pass

    for scheme in ("https", "http"):
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                response = await client.get(f"{scheme}://q1.qlogo.cn/g?b=qq&nk={uid}&s=640")
            if response.status_code == 200 and _is_image_bytes(response.content):
                await asyncio.to_thread(cache_file.write_bytes, response.content)
                return cache_file
        except Exception as exc:
            logger.debug(f"[YRR运势] 头像下载失败 ({scheme}): {exc}")

    return cache_file if cache_file.exists() else None


def _remember_message(message_id: Any, path: Optional[Path]) -> None:
    if path is None or message_id in (None, ""):
        return
    FORTUNE_MSG_MAP[str(message_id)] = path
    while len(FORTUNE_MSG_MAP) > MAX_MESSAGE_MAP:
        FORTUNE_MSG_MAP.popitem(last=False)


def _message_id(receipt: Any) -> Optional[int]:
    if isinstance(receipt, dict):
        value = receipt.get("message_id")
    else:
        value = getattr(receipt, "message_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def load_templates() -> None:
    LOADED_TEMPLATES.clear()
    for index in range(1, 7):
        path = TEMPLATE_DIR / f"{index}.html"
        if not path.is_file():
            logger.warning(f"[YRR运势] 模板缺失: {path}")
            continue
        try:
            LOADED_TEMPLATES[str(index)] = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error(f"[YRR运势] 模板读取失败 {path}: {exc}")


def get_template(choice: str) -> Optional[str]:
    if not LOADED_TEMPLATES:
        load_templates()
    if choice not in LOADED_TEMPLATES:
        choice = "1"
    return LOADED_TEMPLATES.get(choice)


def _font_face_rule() -> str:
    if FONT_FILE_PATH is None:
        return ""
    try:
        uri = FONT_FILE_PATH.resolve().as_uri()
    except (OSError, ValueError):
        return ""
    return (
        "@font-face { font-family: 'MsyhCustom'; "
        f"src: url('{uri}') format('truetype'); }}"
    )


def _as_uri(path: Optional[Path]) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().as_uri()
    except (OSError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------

fortune = on_regex(r"^(?:今日)?运势$", priority=5, block=True)
template_manager = on_regex(r"^运势模板\s*(\d*)$", priority=5, block=True)
background_image_cmd = on_command("背景图", aliases={"运势背景图"}, priority=5, block=True)
reset_all = on_command(
    "重置运势", permission=GROUP_ADMIN | GROUP_OWNER, priority=2, block=True
)


@fortune.handle()
async def handle_fortune(event: MessageEvent) -> None:
    uid = str(event.get_user_id())
    today = datetime.now().strftime("%Y-%m-%d")

    user = await USER_DATA.get(uid)
    if not is_fresh(user, today):
        def _refresh(target: Dict[str, Any]) -> bool:
            normalize_user(target, uid)
            target.update(generate_fortune(uid, today, TEXTS))
            return True

        user = await USER_DATA.mutate(uid, _refresh)

    if user is None:
        await fortune.finish("运势数据读取失败，请稍后再试。")

    level, stars = split_fortune(user.get("fortune", "?"))
    template_choice = str(user.get("template_choice", "1"))
    if template_choice not in TEMPLATE_NAMES:
        template_choice = "1"
    template = get_template(template_choice)
    if not template:
        await fortune.finish("运势模板加载失败，请检查插件文件是否完整。")

    bg_path = await get_background_path()
    avatar_path = await get_avatar_path(uid)
    background_image = _as_uri(bg_path) or background_data_uri()
    avatar_url = _as_uri(avatar_path) or avatar_data_uri(uid)

    context = build_render_context(
        user=user,
        level=level,
        stars=stars,
        background_image=background_image,
        avatar_url=avatar_url,
        font_face_rule=_font_face_rule(),
    )

    try:
        try:
            html = template.format(**context)
            image_bytes = await html_to_pic(
                html,
                viewport=TEMPLATE_VIEWPORTS.get(template_choice, {"width": 1000, "height": 1000}),
                type="jpeg",
                quality=92,
            )
        except FinishedException:
            raise
        except Exception as exc:
            logger.error(f"[YRR运势] 渲染失败，改用文字卡片: {exc}")
            lucky = user.get("lucky_items") or {}
            await fortune.finish(
                f"【今日运势】{level} {stars}\n"
                f"{user.get('fortune_text', '')}\n"
                f"幸运色：{lucky.get('color', '未知')}｜"
                f"幸运数字：{lucky.get('number', '?')}｜"
                f"方位：{lucky.get('direction', '未知')}\n"
                f"宜：{lucky.get('good', '无')}\n"
                f"忌：{lucky.get('bad', '无')}"
            )

        receipt = await fortune.send(MessageSegment.image(image_bytes))
        _remember_message(_message_id(receipt), bg_path)
    finally:
        release_background(bg_path)


@background_image_cmd.handle()
async def handle_background_image(bot: Bot, event: MessageEvent) -> None:
    reply = getattr(event, "reply", None)
    target: Optional[Path] = None
    if reply is not None:
        target = FORTUNE_MSG_MAP.get(str(reply.message_id))
        if target is not None and not target.exists():
            target = None

    if target is None:
        target = await get_background_path()

    if target is None or not target.exists():
        await background_image_cmd.finish("背景图获取失败，请稍后再试。")

    try:
        raw = await asyncio.to_thread(target.read_bytes)
        sent = await bot.send(event, MessageSegment.image(raw))
    except FinishedException:
        raise
    except Exception as exc:
        await background_image_cmd.finish(f"发送失败: {exc}")
        return

    message_id = _message_id(sent)
    if message_id is not None:
        try:
            scheduler.add_job(
                delete_message,
                "date",
                run_date=datetime.now() + timedelta(minutes=2),
                args=(bot, message_id),
                id=f"yrr-fortune-delete-{message_id}",
                replace_existing=True,
                misfire_grace_time=30,
            )
        except Exception as exc:
            logger.warning(f"[YRR运势] 定时撤回任务创建失败: {exc}")


async def delete_message(bot: Bot, message_id: int) -> None:
    try:
        await bot.delete_msg(message_id=message_id)
    except Exception:
        pass


@template_manager.handle()
async def handle_template(event: MessageEvent, groups=RegexGroup()) -> None:
    arg = (groups[0] or "").strip()
    if not arg:
        if HELP_IMAGE_PATH.is_file():
            await template_manager.finish(MessageSegment.image(HELP_IMAGE_PATH))
        await template_manager.finish(
            "可用模板：\n" + "\n".join(f"{key} · {name}" for key, name in TEMPLATE_NAMES.items())
        )

    if arg not in TEMPLATE_NAMES:
        await template_manager.finish("无效模板，请输入 1-6。")

    if not LOADED_TEMPLATES:
        load_templates()
    if arg not in LOADED_TEMPLATES:
        await template_manager.finish(f"模板 {arg} 文件缺失，请联系管理员。")

    uid = str(event.get_user_id())

    def _set_template(target: Dict[str, Any]) -> bool:
        normalize_user(target, uid)
        target["template_choice"] = arg
        return True

    await USER_DATA.mutate(uid, _set_template)
    await template_manager.finish(f"模板已切换为：{TEMPLATE_NAMES[arg]}")


@reset_all.handle()
async def _confirm_reset(matcher: Matcher) -> None:
    await matcher.pause("确认重置所有数据？(回复 y 确认)")


@reset_all.handle()
async def _do_reset(event: MessageEvent) -> None:
    if event.get_plaintext().strip().lower() in {"y", "yes", "是"}:
        def _reset_users(data: Dict[str, Any]) -> None:
            for user in data.get("users", {}).values():
                if isinstance(user, dict):
                    user.update(
                        {
                            "fortune": "?",
                            "fortune_date": "",
                            "fortune_text": "",
                            "radar_data": [],
                            "lucky_items": {},
                        }
                    )

        await USER_DATA.mutate_all(_reset_users)
        await clean_bg_cache()
        schedule_preload()
        await reset_all.finish("已重置。")
    await reset_all.finish("已取消。")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

driver = get_driver()


@driver.on_startup
async def _startup() -> None:
    load_templates()
    logger.info(
        f"[YRR运势] 已加载 {len(LOADED_TEMPLATES)} 套模板；"
        f"数据目录 {DATA_DIR}，缓存目录 {CACHE_DIR}"
    )
    schedule_preload()


@scheduler.scheduled_job("cron", hour=0, minute=0, id="yrr-fortune-daily-cleanup")
async def daily_cleanup() -> None:
    await clean_bg_cache()
    schedule_preload()
    logger.info("[YRR运势] 跨日清理完成。")


@scheduler.scheduled_job("interval", minutes=2, id="yrr-fortune-replenish")
async def replenish_images() -> None:
    if PRELOAD_COUNT and len(PRELOADED_PATHS) < PRELOAD_COUNT:
        schedule_preload()
