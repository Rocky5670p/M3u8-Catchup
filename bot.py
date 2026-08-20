import os
import re
import gc
import time
import json
import asyncio

# Render / Python 3.12+ Event Loop Patch
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

class StopTransmission(Exception):
    pass

API_ID = int(os.environ.get("API_ID", "29968148"))
API_HASH = os.environ.get("API_HASH", "0dc95a4aa9b3514b9db31a4331bf630a")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8919139205:AAGTegOnPybSMJlZJ3RwUimjBcnd4Q8SzFA")

app = Client("M3u8_Downloader_Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {} # task_id -> {"proc": process, "cancelled": False, "url": stream_url, "last_error": "", "engine": ""}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
REFERER = "https://www.zee5.com/"

def make_progress_bar(percentage):
    completed = int(percentage / 10)
    return "█" * completed + "▒" * (10 - completed)

async def fetch_stream_qualities(stream_url):
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-check-certificate",
        "--user-agent", USER_AGENT,
        "--add-header", f"Referer:{REFERER}",
        stream_url
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024 * 10
        )
        stdout, stderr = await process.communicate()
    except Exception:
        return [{"id": "best", "label": "Best Quality"}]
    
    if process.returncode != 0 or not stdout:
        return [{"id": "best", "label": "Best Auto Quality"}]
        
    try:
        data = json.loads(stdout.decode('utf-8', errors='ignore'))
        formats = data.get("formats", [])
        qualities = []
        seen_heights = set()

        for f in formats:
            height = f.get("height")
            format_id = f.get("format_id")
            if height and height not in seen_heights:
                seen_heights.add(height)
                qualities.append({"id": format_id, "label": f"{height}p"})
                
        qualities.sort(key=lambda x: int(x["label"].replace("p", "")), reverse=True)
        return qualities if qualities else [{"id": "best", "label": "Best Quality"}]
    except Exception:
        return [{"id": "best", "label": "Best Quality"}]

async def upload_progress(current, total, message, start_time, file_name, task_id):
    if task_id in ACTIVE_TASKS and ACTIVE_TASKS[task_id].get("cancelled"):
        raise StopTransmission()
        
    now = time.time()
    diff = now - start_time
    if diff < 3:
        return
        
    percentage = (current / total) * 100
    speed = current / diff / (1024 * 1024)
    bar = make_progress_bar(percentage)
    
    text = (
        f"📤 **UPLOADING TO TELEGRAM**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 **File:** `{file_name}`\n"
        f"[{bar}] `{percentage:.1f}%`\n"
        f"🚀 **Speed:** `{speed:.2f} MB/s`\n"
        f"📦 **Size:** `{current / (1024*1024):.1f} MB / {total / (1024*1024):.1f} MB`"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Upload", callback_data=f"cancel_task|{task_id}")]])
    try:
        await message.edit_text(text, reply_markup=markup)
    except Exception:
        pass

async def download_m3u8_stream(cmd, message, task_id, engine_name):
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        limit=1024 * 1024 * 10
    )
    
    ACTIVE_TASKS[task_id]["proc"] = process
    last_update = 0
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    while True:
        if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
            try:
                process.kill()
            except Exception:
                pass
            return -1

        try:
            line = await process.stdout.readline()
        except ValueError:
            line = await process.stdout.read(2048)
            
        if not line:
            break
            
        clean_line = ansi_escape.sub('', line.decode('utf-8', errors='ignore')).strip()

        if "ERROR:" in clean_line or "error" in clean_line.lower():
            ACTIVE_TASKS[task_id]["last_error"] = clean_line

        # Progress reporting logic for various engines
        now = time.time()
        if now - last_update >= 3:
            # yt-dlp match
            match_ytdlp = re.search(r'(\d+\.\d+)%\s+of\s+~\s*([\d\.]+\w+)\s+at\s+([\d\.]+\w+/s)', clean_line)
            # N_m3u8DL-RE match
            match_re = re.search(r'(\d+\.\d+)%\s+([0-9\.]+\s*[M|K|G]?B/s)', clean_line)
            
            if match_ytdlp:
                pct = float(match_ytdlp.group(1))
                bar = make_progress_bar(pct)
                text = (
                    f"📥 **DOWNLOADING STREAM** ({engine_name})\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"[{bar}] `{pct:.1f}%`\n"
                    f"⚡ **Speed:** `{match_ytdlp.group(3)}`\n"
                    f"📦 **Size:** `{match_ytdlp.group(2)}`"
                )
            elif match_re:
                pct = float(match_re.group(1))
                bar = make_progress_bar(pct)
                text = (
                    f"📥 **DOWNLOADING STREAM** ({engine_name})\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"[{bar}] `{pct:.1f}%`\n"
                    f"⚡ **Speed:** `{match_re.group(2)}`"
                )
            else:
                text = (
                    f"📥 **RECORDING STREAM IN PROGRESS**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚙️ **Engine:** `{engine_name}`\n"
                    f"⏳ Capturing segments live..."
                )

            markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Task", callback_data=f"cancel_task|{task_id}")]])
            try:
                await message.edit_text(text, reply_markup=markup)
                last_update = now
            except Exception:
                pass

    await process.wait()
    return process.returncode

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply_text("🔥 **MULTI-ENGINE ADVANCED M3U8 RECORDER** 🔥\n\nBhejiye direct `.m3u8` ya Live Stream link!")

@app.on_message(filters.regex(r"https?://[^\s]+"))
async def link_handler(client, message):
    stream_url = message.text.strip()
    task_id = str(int(time.time()))
    ACTIVE_TASKS[task_id] = {"url": stream_url, "cancelled": False, "last_error": "", "engine": "ytdlp"}

    # Step 1: Select Engine First
    buttons = [
        [
            InlineKeyboardButton("⚡ N_m3u8DL-RE (Fastest)", callback_data=f"engine|{task_id}|nm3u8dl"),
            InlineKeyboardButton("🎬 Streamlink (AIO)", callback_data=f"engine|{task_id}|streamlink")
        ],
        [
            InlineKeyboardButton("🛠 FFmpeg Engine", callback_data=f"engine|{task_id}|ffmpeg"),
            InlineKeyboardButton("📥 yt-dlp Native", callback_data=f"engine|{task_id}|ytdlp")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_task|{task_id}")]
    ]

    await message.reply_text(
        f"⚙️ **Select Recording Engine:**\n\n"
        f"🔗 **URL:** `{stream_url[:50]}...`\n\n"
        f"💡 *Tip: Complex PHP/Zee5 links ke liye `Streamlink` ya `N_m3u8DL-RE` use karein.*",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex(r"^engine\|"))
async def select_engine_callback(client, callback_query: CallbackQuery):
    _, task_id, engine = callback_query.data.split("|")
    
    if task_id not in ACTIVE_TASKS or ACTIVE_TASKS[task_id]["cancelled"]:
        await callback_query.message.edit_text("❌ **Task Expired or Cancelled.**")
        return
        
    ACTIVE_TASKS[task_id]["engine"] = engine
    stream_url = ACTIVE_TASKS[task_id]["url"]
    
    await callback_query.message.edit_text(f"🔍 **Fetching Qualities with engine:** `{engine.upper()}`...")

    qualities = await fetch_stream_qualities(stream_url)
    
    buttons = []
    row = []
    for q in qualities:
        cb_data = f"start_dl|{task_id}|{q['id']}"
        row.append(InlineKeyboardButton(f"🎬 {q['label']}", callback_data=cb_data))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
        
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_task|{task_id}")])

    await callback_query.message.edit_text(
        f"✅ **Engine Set:** `{engine.upper()}`\n\nChoose preferred Quality:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query(filters.regex(r"^cancel_task\|"))
async def cancel_task_callback(client, callback_query: CallbackQuery):
    task_id = callback_query.data.split("|")[1]
    if task_id in ACTIVE_TASKS:
        ACTIVE_TASKS[task_id]["cancelled"] = True
        proc = ACTIVE_TASKS[task_id].get("proc")
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        await callback_query.answer("🛑 Task Cancelled!")
        await callback_query.message.edit_text("❌ **Process Cancelled by User.**")
    else:
        await callback_query.answer("No active task found.")

@app.on_callback_query(filters.regex(r"^start_dl\|"))
async def start_download_callback(client, callback_query: CallbackQuery):
    await callback_query.answer("⚡ Starting Download...")
    
    _, task_id, format_id = callback_query.data.split("|")
    
    if task_id not in ACTIVE_TASKS or ACTIVE_TASKS[task_id]["cancelled"]:
        await callback_query.message.edit_text("❌ **Task Expired or Cancelled.**")
        return

    stream_url = ACTIVE_TASKS[task_id]["url"]
    engine = ACTIVE_TASKS[task_id].get("engine", "ytdlp")
    output_file = f"Stream_{task_id}.mkv"

    await callback_query.message.edit_text(f"🚀 **Initializing {engine.upper()} Engine...**")

    # Command builder according to user selection
    if engine == "nm3u8dl":
        cmd = [
            "N_m3u8DL-RE",
            stream_url,
            "-H", f"User-Agent: {USER_AGENT}",
            "-H", f"Referer: {REFERER}",
            "--save-name", f"Stream_{task_id}",
            "--auto-select",
            "--tmp-dir", ".",
            "--save-dir", ".",
            "-M", "format=mkv:muxer=ffmpeg"
        ]
    elif engine == "streamlink":
        cmd = [
            "streamlink",
            "--http-header", f"User-Agent={USER_AGENT}",
            "--http-header", f"Referer={REFERER}",
            "--default-stream", "best",
            stream_url,
            "-o", output_file
        ]
    elif engine == "ffmpeg":
        cmd = [
            "ffmpeg",
            "-allowed_extensions", "ALL",
            "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
            "-headers", f"User-Agent: {USER_AGENT}\r\nReferer: {REFERER}\r\n",
            "-i", stream_url,
            "-c", "copy",
            output_file
        ]
    else: # Default yt-dlp
        cmd = [
            "yt-dlp",
            "--newline",
            "--no-check-certificate",
            "--downloader", "m3u8:native",
            "--hls-use-mpegts",
            "-f", format_id,
            "--fragment-retries", "20",
            "--concurrent-fragments", "1",
            "--user-agent", USER_AGENT,
            "--add-header", f"Referer:{REFERER}",
            stream_url,
            "-o", output_file
        ]

    try:
        ret_code = await download_m3u8_stream(cmd, callback_query.message, task_id, engine.upper())

        if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
            return

        if not os.path.exists(output_file) and os.path.exists(f"Stream_{task_id}.mp4"):
            output_file = f"Stream_{task_id}.mp4"

        if ret_code != 0 or not os.path.exists(output_file):
            err_msg = ACTIVE_TASKS[task_id].get("last_error", "Download failed with selected engine.")
            await callback_query.message.edit_text(f"❌ **Download Failed!**\n\n`{err_msg}`")
            return

        await callback_query.message.edit_text("📤 **Preparing to Upload to Telegram...**")
        start_time_stamp = time.time()

        await client.send_video(
            chat_id=callback_query.message.chat.id,
            video=output_file,
            caption=f"📺 **Recording Complete!**\n⚙️ **Engine:** `{engine.upper()}`",
            progress=upload_progress,
            progress_args=(callback_query.message, start_time_stamp, output_file, task_id)
        )
        await callback_query.message.delete()

    except StopTransmission:
        await callback_query.message.edit_text("❌ **Upload Cancelled by User.**")
    except Exception as e:
        await callback_query.message.edit_text(f"⚠️ **Error:** `{str(e)}`")
    finally:
        if os.path.exists(output_file):
            os.remove(output_file)
        if task_id in ACTIVE_TASKS:
            del ACTIVE_TASKS[task_id]
        gc.collect()

async def dummy_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = await asyncio.start_server(lambda r, w: w.close(), "0.0.0.0", port)
    await server.serve_forever()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(dummy_web_server())
    app.run()
