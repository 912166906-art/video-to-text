import re
import json
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse
import ipaddress
import socket

import streamlit as st
import yt_dlp
from opencc import OpenCC

converter = OpenCC("t2s")

st.set_page_config(page_title="网页视频转文字助手", page_icon="🎬", layout="centered")

MAX_DURATION = 45 * 60
MAX_FILE_MB = 350



def valid_public_url(url: str):
    url = (url or '').strip()
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname:
        raise ValueError('请输入完整的 http:// 或 https:// 视频网址。')
    hostname = parsed.hostname.lower().rstrip('.')
    if hostname in ('localhost',) or hostname.endswith(('.localhost','.local','.internal')):
        raise ValueError('不支持内网地址。')
    try:
        infos = socket.getaddrinfo(hostname, None)
        if not infos or any(not ipaddress.ip_address(item[4][0]).is_global for item in infos):
            raise ValueError('只能访问公网视频网站。')
    except socket.gaierror:
        raise ValueError('无法解析网址域名。')
    return url



def url_candidates(original):
    """Try public, recognizable URLs. Never fabricate authenticated access."""
    original = valid_public_url(original)
    parsed = urlparse(original)
    host = (parsed.hostname or '').lower()
    options = [original]

    # Tracking query parameters are often unnecessary on a playback URL.
    tracking = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_content',
                'utm_term', 'from', 'share', 'share_source', 'spm',
                'externalsource', 'tc', 'dc', 'f', 'to'}
    from urllib.parse import parse_qsl, urlencode
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k.lower() not in tracking]
    if len(kept) < len(pairs):
        options.append(urlunparse(parsed._replace(query=urlencode(kept))))

    # MGTV's /l/{show}/{video}.html share format can also have a /b/ page.
    # Candidate only, NOT an assurance that the address exists.
    if host in ('m.mgtv.com', 'www.mgtv.com', 'mgtv.com'):
        match = re.fullmatch(r'/l/(\d+)/(\d+)\.html', parsed.path)
        if match:
            options.append(f'https://www.mgtv.com/b/{match.group(1)}/{match.group(2)}.html')
        if host == 'm.mgtv.com' and parsed.path.startswith('/b/'):
            options.append(urlunparse(parsed._replace(netloc='www.mgtv.com', query='')))

    unique = []
    for candidate in options:
        if candidate not in unique:
            valid_public_url(candidate)
            unique.append(candidate)
    return unique


def user_error(exc):
    msg = str(exc)
    lower = msg.lower()
    if 'unsupported url' in lower or 'no suitable' in lower:
        return '当前网站或链接格式不受解析器支持。可尝试视频网站的正式播放页，或者上传你有权使用的视频文件。'
    if '403' in lower or 'forbidden' in lower or 'blocked' in lower:
        return '视频平台拒绝了云服务器的访问（403）。换地址通常无效；可以尝试官方提供的可访问链接或上传授权文件。'
    if any(word in lower for word in ('sign in','login','cookies','authentication','private video')):
        return '该视频需要登录或访问授权。公共云服务器无法直接使用你浏览器里的登录状态。'
    if any(word in lower for word in ('drm','protected content','encrypted')):
        return '该视频可能受 DRM 加密保护，工具不会尝试绕过保护。'
    if 'requested format is not available' in lower:
        return '未找到可以提取的音视频格式；已尝试自动选择可用格式。'
    if 'timed out' in lower or 'timeout' in lower:
        return '网站连接超时，请确认链接可在浏览器打开，或稍后重新尝试。'
    return '当前链接未能完成处理。可尝试上传已获授权的视频或音频文件。'


def find_video_info(url, workdir, status):
    options = url_candidates(url)
    reasons = []
    for index, candidate in enumerate(options, 1):
        status(f'正在尝试解析地址（第 {index}/{len(options)} 种格式）...')
        try:
            with yt_dlp.YoutubeDL({**ydl_base(workdir), 'skip_download': True,
                                  'ignoreerrors': False, 'cachedir': False}) as ydl:
                info = ydl.extract_info(candidate, download=False)
            if info and info.get('_type') != 'playlist':
                return candidate, info
            reasons.append('未找到独立视频')
        except Exception as exc:
            reasons.append(str(exc)[:350])
    reason = user_error(' | '.join(reasons))
    raise ValueError(reason + f'（已尝试 {len(options)} 种链接格式）')

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
    url, info = find_video_info(url, workdir, status)
    if not info: raise ValueError('无法读取此链接；部分平台需要登录、限制了访问或使用DRM。')
    duration = info.get('duration')
    if duration and duration > MAX_DURATION:
        raise ValueError('当前版本限制单个视频不超过45分钟。')
    title = info.get('title') or '网页视频'
    caption = select_caption(info, lang)
    if caption:
        key, ext, automatic = caption
        status('检测到字幕，正在获取...')
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
    status('没有找到可用的字幕，正在提取音频...')
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
        raise ValueError('网页音频提取失败，可能是版权保护或网站不支持。请尝试上传本地视频。')
    return transcribe(wavs[0],lang,status), '本地AI语音识别', title

_whisper_model = None
def transcribe(path, lang, status):
    global _whisper_model
    status('正在语音识别（首次运行会下载模型）...')
    from faster_whisper import WhisperModel
    if _whisper_model is None:
        _whisper_model = WhisperModel('small', device='cpu', compute_type='int8')
    code = 'zh' if lang == '中文' else 'en' if lang == 'English' else None
    segments, _ = _whisper_model.transcribe(str(path), language=code, vad_filter=True, beam_size=3)
    return [(seg.start,seg.end,seg.text.strip()) for seg in segments if seg.text.strip()]

def from_upload(file, lang, status, workdir):
    path = Path(file)
    if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
        raise ValueError('单个上传文件不能超过350MB。')
    audio = workdir / 'upload.wav'
    status('正在提取音频...')
    cmd = ['ffmpeg','-v','error','-y','-i',str(path),'-vn','-ac','1','-ar','16000','-t',str(MAX_DURATION),str(audio)]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if process.returncode != 0 or not audio.exists():
        raise ValueError('无法读取上传文件的音频轨道，请确认文件格式。')
    return transcribe(audio,lang,status), '本地AI语音识别', path.stem



st.title('🎬 网页视频转文字助手')
st.caption('自动尝试不同链接格式，优先获取字幕；无字幕则进行 AI 语音转写。支持简体中文 TXT / SRT 导出。')
url = st.text_input('视频网页链接', placeholder='https://example.com/video')
uploaded = st.file_uploader('或上传视频 / 音频文件（上传文件优先）', type=['mp4','mov','mkv','webm','mp3','wav','m4a','aac','flac'])
lang = st.selectbox('识别语言', ['中文', 'English', '自动识别'])
if st.button('开始提取', type='primary', use_container_width=True):
    if not url.strip() and uploaded is None:
        st.warning('请粘贴链接或上传文件。')
    else:
        status_area = st.empty()
        def status(msg): status_area.info(msg)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                if uploaded is not None:
                    if uploaded.size > MAX_FILE_MB * 1024 * 1024:
                        raise ValueError('文件大于350 MB，无法处理。')
                    path = work / ('input' + Path(uploaded.name).suffix.lower())
                    path.write_bytes(uploaded.getbuffer())
                    segments, source, title = from_upload(str(path), lang, status, work)
                else:
                    segments, source, title = from_url(url.strip(), lang, status, work)
            status_area.empty()
            if not segments:
                st.warning('未检测到可识别的语音或字幕。')
            else:
                # 统一转换为简体中文，确保网页和导出文件一致。
                segments = [(s, e, converter.convert(t)) for s, e, t in segments]
                transcript = '\n'.join(f'[{fmt_clock(s)}] {t}' for s,e,t in segments)
                srt = '\n\n'.join(f'{i}\n{fmt_srt_time(s)} --> {fmt_srt_time(e)}\n{t}' for i,(s,e,t) in enumerate(segments,1))
                st.session_state['result'] = (title,source,transcript,srt,len(segments))
        except Exception as exc:
            status_area.empty()
            st.error('处理失败：' + user_error(exc) if not isinstance(exc, ValueError) else '处理失败：' + str(exc)[:500])
            with st.expander('查看技术错误（用于反馈问题）'):
                st.code(str(exc)[:1500])
            st.info('备用方案：如果你有权访问该素材，可以将视频或音频文件下载到电脑，再使用上方“上传文件”功能。')
if 'result' in st.session_state:
    title,source,transcript,srt,count = st.session_state['result']
    st.success(f'完成：{title}｜{source}｜共 {count} 段')
    st.text_area('带时间码的逐字稿（可选中复制）', transcript, height=400)
    col1,col2=st.columns(2)
    with col1:
        st.download_button('下载 TXT', transcript.encode('utf-8-sig'), 'transcript.txt', mime='text/plain', use_container_width=True)
    with col2:
        st.download_button('下载 SRT', srt.encode('utf-8-sig'), 'subtitles.srt', mime='text/plain', use_container_width=True)
st.info('只能处理有权访问的资源。部分平台的登录、地域限制、DRM或反爬措施仍可能导致链接失败；上传授权文件是备用办法。免费云服务器不适合并发处理大量长视频。')
