---
title: 视频字幕与语音提取工具
emoji: 🎬
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: 5.49.1
app_file: app.py
pinned: false
license: mit
---

# 视频字幕与语音提取工具

这是一个可以部署到 Hugging Face Spaces 的网页工具。使用者不需要在电脑上安装 Python 或 FFmpeg。

## 只用浏览器部署（适合零基础）

1. 打开 https://huggingface.co/join 注册 Hugging Face 账号并登录。
2. 打开 https://huggingface.co/new-space ，为 Space 起英文名，例如 `my-video-transcriber`。
3. 选择 **Gradio** 作为 SDK，选择 **Public** 或 **Private**（建议优先 Private，具体能否免付费取决于平台当时的套餐和资源政策），创建 Space。
4. 在 Space 的 **Files / Files and versions** 页面点 **Add file → Upload files**，上传本压缩包内的 `app.py`、`requirements.txt`、`packages.txt` 和本 `README.md`。如果在线创建 Space 时已有 README，请替换其内容为这里的 README，保留顶部 `---` 包围的 YAML 配置。
5. 等待 **Building** → **Running**，点击 **App** 标签。云端会安装 FFmpeg、Python 依赖，以及首次识别时的 Whisper 模型。
6. 如果提示内存不足或硬件不可用，需要更换 Space 资源规格（可能产生费用）；免费 CPU 配置不保证可运行所有时长的视频。
7. 输入你有权访问的视频网页地址，点“开始提取”；也可直接上传本地视频/音频。完成后下载 TXT 和 SRT。

## 能做什么

- 优先尝试读取网页视频的已有字幕（包括部分平台的自动字幕）；
- 如果没有可用字幕，下载有权访问的音频，用 Faster-Whisper 本地模型转写；
- 返回带时间码的文本，以及 TXT、SRT 下载文件；
- 支持视频或音频文件直接上传。

## 使用限制与排查

- 并非所有网站受 `yt-dlp` 支持。需要登录、特殊 Cookie、DRM、反爬的网站通常无法处理。本项目不会提供绕过授权限制的能力。
- 视频单条上限 45 分钟，上传文件 350 MB；云端 CPU 处理可能很慢。
- 网页端不能绕过网站跨域、安全访问限制，因此所有链接解析在后端运行。
- 这是一份可部署代码，未在用户专属 Hugging Face 环境实测，依赖和平台版本更新可能需要调整。
- 如果 Space 出现运行错误，到 Logs 中查看报错。可将日志复制给 ChatGPT 寻求修复。
- 如果启用 Public Space，不要上传私密、内部或尚未授权公开的采访内容；建议使用私有部署并确认平台数据政策。

## 本地运行（可选）

安装 Python、系统 FFmpeg 后：`pip install -r requirements.txt`，再执行 `python app.py`。
