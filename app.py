import os
import re
import json
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import urlparse
import ipaddress
import socket

import gradio as gr
import yt_dlp

MAX_DURATION = 45 * 60
MAX_FILE_MB = 350

CSS = """
.gradio-container {max-width: 960px !important; margin: auto !important}
#title {text-align:center;margin-bottom:8px}
"""

def valid_public_url(url: str):
    url = (url or '').strip()
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname:
        raise gr.Error('请输入完整的 http:// 或 https:// 视频网址。')
    hostname = parsed.hostname.lower().rstrip('.')
    if hostname in ('localhost',) or hostname.endswith(('.localhost','.local','.internal')):
        raise gr.Error('不支持内网地址。')
    try:
        infos = socket.getaddrinfo(hostname, None)
        if not infos or any(not ipaddress.ip_address(item[4][0]).is_global for item in infos):
            raise gr.Error('只能访问公网视频网站。')
    except socket.gaierror:
        raise gr.Error('无法解析网址域名。')
    return url

def fmt_srt_time(sec):
    sec = max(0, float(sec))
    millis = int(round(sec * 1000))
    h, rem = divmod(millis, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

def fmt_clock(sec):
    total = int(max(0, float(sec)))
    h, r = divmod(total, 3600)
    m, s = divmod(r, 60)
    return f'{h:02d}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'

def clean_text(s):
    return re.sub(r'<[^>]*>', '', str(s)).replace('&nbsp;', ' ').strip()

def parse_subtitles(path):
    raw = Path(path).read_text(encoding='utf-8-sig', errors='replace')
    if path.suffix.lower() == '.json3' or raw.lstrip().startswith('{'):
        data = json.loads(raw)
        result = []
        for event in data.get('events', []):
            txt = ''.join(x.get('utf8', '') for x in event.get('segs', []))
            txt = clean_text(txt).replace('\n', ' ')
            if txt:
                start = event.get('tStartMs', 0)/1000
                end = start + event.get('dDurationMs', 3000)/1000
                result.append((start, end, txt))
        return result
    # VTT / SRT: split cue blocks and parse the timestamp line.
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    cue_re = re.compile(r'(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})')
    result = []
    for chunk in re.split(r'\n\s*\n', raw):
        lines = chunk.strip().splitlines()
        for idx, line in enumerate(lines):
            match = cue_re.search(line)
            if not match:
                continue
            groups = match.groups()
            def seconds(parts):
                hh, mm, ss, ms = parts
                return int(hh or 0)*3600+int(mm)*60+int(ss)+int(ms.ljust(3, '0'))/1000
            txt = clean_text(' '.join(lines[idx+1:]))
            txt = re.sub(r'\{\\an\d+\}', '', txt).strip()
            if txt:
                result.append((seconds(groups[:4]),seconds(groups[4:]),txt))
            break
    # Automatic caption tracks sometimes include repeated rolling text.
    cleaned = []
    for start,end,txt in result:
        if cleaned and cleaned[-1][2] == txt:
            continue
        cleaned.append((start,end,txt))
    return cleaned

def select_caption(info, lang):
    subs = info.get('subtitles') or {}
    auto = info.get('automatic_captions') or {}
    all_keys = list(subs) + list(auto)
    if lang == '中文':
        pref = ['zh-Hans','zh-CN','zh','zh-Hant','zh-TW','en']
    elif lang == 'English':
        pref = ['en','en-US','en-GB','zh-Hans','zh']
    else:
        pref = list(subs) + list(auto)
    keys = pref + [k for k in all_keys if k not in pref]
    for source, automatic in ((subs,False),(auto,True)):
        for key in keys:
            if key not in source: continue
            choices = sorted(source[key], key=lambda x: (x.get('ext') not in ('vtt','srt','json3'),))
            for entry in choices:
                if entry.get('ext') in ('vtt','srt','json3') and entry.get('url'):
                    return key, entry.get('ext'), automatic
    return None

def ydl_base(workdir):
    return dict(paths={'home':str(workdir)}, noplaylist=True, quiet=True, no_warnings=True, socket_timeout=15, retries=2, fragment_retries=2, restrictfilenames=True)

def from_url(url, lang, status, workdir):
    url = valid_public_url(url)
    with yt_dlp.YoutubeDL({**ydl_base(workdir), 'skip_download':True, 'ignoreerrors':False}) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info: raise gr.Error('无法读取此链接；部分平台需要登录、限制了访问或使用DRM。')
    duration = info.get('duration')
    if duration and duration > MAX_DURATION:
        raise gr.Error('当前版本限制单个视频不超过45分钟。')
    title = info.get('title') or '网页视频'
    caption = select_caption(info, lang)
    if caption:
        key, ext, automatic = caption
        status(gr.update(value='检测到字幕，正在获取...'))
        opts = {**ydl_base(workdir), 'skip_download':True, 'writesubtitles':not automatic,
                'writeautomaticsub':automatic, 'subtitleslangs':[key], 'subtitlesformat':ext,
                'outtmpl':{'default': str(workdir / 'media.%(ext)s')}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            tracks = sorted([p for p in workdir.rglob('*') if p.suffix.lower() in ('.vtt','.srt','.json3')])
            for track in tracks:
                segments = parse_subtitles(track)
                if segments:
                    return segments, f'直接提取{"自动生成" if automatic else "网站提供"}的 {key} 字幕', title
        except Exception:
            pass
    status(gr.update(value='没有找到可用的字幕，正在提取音频...'))
    opts = {**ydl_base(workdir), 'format':'bestaudio/best',
            'outtmpl':{'default':str(workdir / 'source.%(ext)s')},
            'postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'wav'}],
            'postprocessor_args':['-ar','16000','-ac','1'],
            'max_filesize':MAX_FILE_MB * 1024 * 1024}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    audios = list(workdir.glob('source.*'))
    wavs = [p for p in audios if p.suffix.lower()=='.wav']
    if not wavs:
        raise gr.Error('网页音频提取失败，可能是版权保护或网站不支持。请尝试上传本地视频。')
    return transcribe(wavs[0],lang,status), '本地AI语音识别', title

_whisper_model = None
def transcribe(path, lang, status):
    global _whisper_model
    status(gr.update(value='正在语音识别（首次运行会下载模型）...'))
    from faster_whisper import WhisperModel
    if _whisper_model is None:
        _whisper_model = WhisperModel('small', device='cpu', compute_type='int8')
    code = 'zh' if lang == '中文' else 'en' if lang == 'English' else None
    segments, _ = _whisper_model.transcribe(str(path), language=code, vad_filter=True, beam_size=3)
    return [(seg.start,seg.end,seg.text.strip()) for seg in segments if seg.text.strip()]

def from_upload(file, lang, status, workdir):
    path = Path(file)
    if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
        raise gr.Error('单个上传文件不能超过350MB。')
    audio = workdir / 'upload.wav'
    status(gr.update(value='正在提取音频...'))
    cmd = ['ffmpeg','-v','error','-y','-i',str(path),'-vn','-ac','1','-ar','16000','-t',str(MAX_DURATION),str(audio)]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if process.returncode != 0 or not audio.exists():
        raise gr.Error('无法读取上传文件的音频轨道，请确认文件格式。')
    return transcribe(audio,lang,status), '本地AI语音识别', path.stem

def process(url, file, language, progress=gr.Progress()):
    if not (url or file):
        raise gr.Error('请粘贴视频链接，或上传一个视频/音频文件。')
    progress(0.02,desc='开始处理')
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        def status(_): pass
        try:
            if file:
                segments, source, title = from_upload(file,language,status,work)
            else:
                segments, source, title = from_url(url,language,status,work)
        except gr.Error: raise
        except Exception as exc:
            raise gr.Error('处理失败：'+str(exc)[:220]) from None
        if not segments:
            raise gr.Error('未检测到可识别的语音或字幕。')
        lines = [f'[{fmt_clock(s)}] {t}' for s,e,t in segments]
        transcript = '\n'.join(lines)
        srt = '\n\n'.join(f'{i}\n{fmt_srt_time(s)} --> {fmt_srt_time(e)}\n{t}' for i,(s,e,t) in enumerate(segments,1))
        output_dir = Path(tempfile.mkdtemp(prefix='video_text_result_'))
        txt_file = output_dir / 'transcript.txt'
        srt_file = output_dir / 'subtitles.srt'
        txt_file.write_text(transcript,encoding='utf-8')
        srt_file.write_text(srt,encoding='utf-8')
        progress(1.0,desc='完成')
        return f'✅ 已完成：{title}｜来源：{source}｜共 {len(segments)} 段', transcript, str(txt_file), str(srt_file)

with gr.Blocks(title='网页视频转文字助手',css=CSS,theme=gr.themes.Soft()) as demo:
    gr.Markdown('# 🎬 网页视频转文字助手\n**输入链接或上传视频 → 优先提取字幕 → 无字幕时转写音频 → 导出时间码文字稿**',elem_id='title')
    with gr.Row():
        url=gr.Textbox(label='视频网页链接',placeholder='https://www.example.com/video/...',scale=4)
    uploaded=gr.File(label='或者上传视频 / 音频（上传优先）',file_types=['video','audio'],type='filepath')
    language=gr.Dropdown(['中文','English','自动识别'],value='中文',label='识别语言')
    button=gr.Button('开始提取',variant='primary')
    result=gr.Markdown('尚未开始处理')
    text=gr.Textbox(label='带时间码的逐字稿',lines=18,show_copy_button=True)
    with gr.Row():
        txt=gr.File(label='下载 TXT')
        srt=gr.File(label='下载 SRT')
    gr.Markdown('''**注意：** 不保证所有网站可提取。需要登录、付费授权、DRM加密或防爬限制的网站可能失败；本工具不会绕过这些限制。请只处理有权使用的内容。上传视频不会被公开，但会经过你部署的云服务器处理。免费云服务器可能休眠，首次语音识别可能较慢。''')
    button.click(process,[url,uploaded,language],[result,text,txt,srt])

demo.queue(max_size=5,default_concurrency_limit=1)
if __name__ == '__main__':
    demo.launch()
