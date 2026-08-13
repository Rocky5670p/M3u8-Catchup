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

ACTIVE_TASKS = {} # task_id -> {"proc": process, "cancelled": False, "url": stream_url, "last_error": ""}

def make_progress_bar(percentage):
    completed = int(percentage / 10)
    return "█" * completed + "▒" * (10 - completed)

async def fetch_stream_qualities(stream_url):
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-check-certificate",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
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
        return None
    
    if process.returncode != 0 or not stdout:
        return None
        
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

async def download_m3u8_stream(cmd, message, task_id):
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

        if "ERROR:" in clean_line:
            ACTIVE_TASKS[task_id]["last_error"] = clean_line

        if "[download]" in clean_line and "%" in clean_line:
            now = time.time()
            if now - last_update >= 3:
                match = re.search(r'(\d+\.\d+)%\s+of\s+~\s*([\d\.]+\w+)\s+at\s+([\d\.]+\w+/s)', clean_line)
                if match:
                    pct = float(match.group(1))
                    size = match.group(2)
                    speed = match.group(3)
                    bar = make_progress_bar(pct)

                    text = (
                        f"📥 **DOWNLOADING CATCHUP STREAM**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"[{bar}] `{pct:.1f}%`\n"
                        f"⚡ **Speed:** `{speed}`\n"
                        f"📦 **Approx Size:** `{size}`"
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
    await message.reply_text("🔥 **M3U8 ADVANCED CATCHUP DOWNLOADER** 🔥\n\nDirect Stream / Catchup `.m3u8` link bhejo!")

@app.on_message(filters.regex(r"https?://[^\s]+"))
async def link_handler(client, message):
    stream_url = message.text.strip()
    status_msg = await message.reply_text("🔍 **Fetching Stream Qualities...**\n`Validating if stream is supported...`")
    
    qualities = await fetch_stream_qualities(stream_url)
    
    if not qualities:
        await status_msg.edit_text("❌ **Stream Not Supported!**\n\nLink invalid hai, stream offline hai, ya DRM encryption hai.")
        return

    task_id = str(int(time.time()))
    ACTIVE_TASKS[task_id] = {"url": stream_url, "cancelled": False, "last_error": ""}

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

    await status_msg.edit_text(
        f"✅ **Stream Supported!**\n\nChoose preferred Download Quality:",
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
    output_file = f"Stream_{task_id}.mkv"

    await callback_query.message.edit_text("🚀 **Initializing Downloader... Please wait...**")
    
    # FIX: Added --hls-prefer-native to bypass ffmpeg network crash on Cloudflare
    cmd = [
        "yt-dlp",
        "--newline",
        "--no-check-certificate",
        "--hls-prefer-native",
        "-f", format_id,
        "--fragment-retries", "10",
        "--concurrent-fragments", "2",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        stream_url,
        "-o", output_file
    ]

    try:
        ret_code = await download_m3u8_stream(cmd, callback_query.message, task_id)

        if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
            return

        if ret_code != 0 or not os.path.exists(output_file):
            err_msg = ACTIVE_TASKS[task_id].get("last_error", "Stream fragments download failed.")
            await callback_query.message.edit_text(f"❌ **Download Failed!**\n\n`{err_msg}`")
            return

        await callback_query.message.edit_text("📤 **Preparing to Upload...**")
        start_time_stamp = time.time()

        await client.send_video(
            chat_id=callback_query.message.chat.id,
            video=output_file,
            caption=f"📺 **Recording Complete!**",
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
