#!/usr/bin/env python3
"""
Bloomberg Headline Grabber

抓取 Bloomberg 最新头条新闻，提取完整正文，并通过邮件发送。

工作流：
  1. 从 Bloomberg sitemap XML 获取最新文章列表（无需认证）
  2. 通过 Lightpanda headless 浏览器提取 __NEXT_DATA__ 完整正文（默认）
     或通过 macOS Chrome AppleScript 提取（--backend chrome）
  3. 解析递归 JSON 文档树，渲染为 HTML
  4. 通过 SMTP 发送 HTML 邮件

使用方法：
  python3 bloomberg_grabber.py --count 5
  python3 bloomberg_grabber.py --to 1188540@qq.com --count 5
  python3 bloomberg_grabber.py --count 3 --dry-run
  python3 bloomberg_grabber.py --skip-extract --count 10

环境变量：
  BLOOMBERG_SMTP_HOST / BLOOMBERG_SMTP_PORT / BLOOMBERG_SMTP_USER / BLOOMBERG_SMTP_PASS
"""

import argparse
import email.mime.multipart
import email.mime.text
import html as html_module
import json
import os
import re
import shutil
import smtplib
import ssl
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import requests

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
SITEMAP_URL = "https://www.bloomberg.com/feeds/sitemap_news.xml"
NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
}
DEFAULT_TO = "1188540@qq.com"
BLOOMBERG_HOME = "https://www.bloomberg.com"
LIGHTPANDA_TIMEOUT = 25  # seconds for lightpanda fetch
# 首页模块优先级：lede 是头版头条，well-* 是次级头条区
HOME_MODULE_ORDER = ["lede", "well-1", "well-2", "well-3", "well-4", "more-top-stories", "latest"]
CHROME_TIMEOUT = 50  # seconds total for AppleScript


# ─────────────────────────────────────────────
# 1. Fetch headline list from sitemap
# ─────────────────────────────────────────────
def fetch_headlines(count: int = 10, source: str = "homepage") -> list[dict]:
    """
    获取 Bloomberg 头条新闻列表。

    source="homepage"（默认）：从 Bloomberg 首页 __NEXT_DATA__ 提取编辑精选头条。
    source="sitemap"：从 sitemap XML 获取最新发布的文章（按时间排序）。
    """
    if source == "homepage":
        try:
            return _fetch_headlines_from_homepage(count)
        except Exception as e:
            print(f"  ⚠️  首页提取失败 ({e})，fallback 到 sitemap")
            return _fetch_headlines_from_sitemap(count)
    return _fetch_headlines_from_sitemap(count)


def _fetch_headlines_from_homepage(count: int) -> list[dict]:
    """从 Bloomberg 首页 __NEXT_DATA__ 提取编辑精选头条。"""
    if not _lightpanda_available():
        raise RuntimeError("lightpanda 不可用")

    wait_expr = (
        "document.getElementById('__NEXT_DATA__') && "
        "document.getElementById('__NEXT_DATA__').textContent.length > 100000"
    )

    result = subprocess.run(
        [
            "lightpanda", "fetch", BLOOMBERG_HOME,
            "--dump", "html",
            "--wait-ms", str(LIGHTPANDA_TIMEOUT * 1000),
            "--wait-script", wait_expr,
            "--json",
            "--log-level", "error",
        ],
        capture_output=True,
        text=True,
        timeout=LIGHTPANDA_TIMEOUT + 10,
    )

    if result.returncode != 0:
        raise RuntimeError(f"lightpanda exit {result.returncode}")

    # 解析 JSON 输出
    html_content = ""
    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if "content" in obj:
                html_content = obj["content"]
                break
        except json.JSONDecodeError:
            continue

    if not html_content:
        raise RuntimeError("no HTML content in output")

    # 提取 __NEXT_DATA__
    idx = html_content.find("__NEXT_DATA__")
    if idx < 0:
        raise RuntimeError("no __NEXT_DATA__ on homepage")

    start = html_content.find(">", idx) + 1
    end = html_content.find("</script>", start)
    nd = json.loads(html_content[start:end].strip())

    modules = nd.get("props", {}).get("pageProps", {}).get("initialState", {}).get("modulesById", {})

    seen_urls = set()
    articles = []

    for module_id in HOME_MODULE_ORDER:
        mod = modules.get(module_id, {})
        items = mod.get("items", [])
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url", "")
            if not url:
                continue

            full_url = f"https://www.bloomberg.com{url}" if url.startswith("/") else url
            clean_url = full_url.split("?")[0]
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            img = item.get("image", {})
            image_url = img.get("baseUrl", "") if isinstance(img, dict) else ""

            articles.append({
                "title": item.get("headline", ""),
                "url": full_url,
                "published": item.get("publishedAt", ""),
                "image": image_url,
            })

            if len(articles) >= count:
                break
        if len(articles) >= count:
            break

    return articles


def _fetch_headlines_from_sitemap(count: int) -> list[dict]:
    """从 Bloomberg sitemap 获取最新文章列表（按发布时间排序）。"""
    for attempt in range(3):
        try:
            resp = requests.get(SITEMAP_URL, timeout=30)
            resp.raise_for_status()
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt == 2:
                raise
            print(f"  ⚠️  Sitemap 请求超时，重试 ({attempt+1}/3)...")
            time.sleep(2)
    root = ET.fromstring(resp.content)

    articles = []
    for url_elem in root.findall("sm:url", NS):
        loc = url_elem.find("sm:loc", NS)
        loc_text = loc.text.strip() if loc is not None and loc.text else ""

        news_elem = url_elem.find("news:news", NS)
        if news_elem is None:
            continue

        title_el = news_elem.find("news:title", NS)
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        pub_el = news_elem.find("news:publication_date", NS)
        pub = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

        img_el = url_elem.find("image:image", NS)
        img = ""
        if img_el is not None:
            iloc = img_el.find("image:loc", NS)
            img = iloc.text.strip() if iloc is not None and iloc.text else ""

        articles.append({
            "title": title,
            "url": loc_text,
            "published": pub,
            "image": img,
        })

        if len(articles) >= count * 3:
            break

    articles.sort(key=lambda a: a["published"], reverse=True)
    return articles[:count]


# ─────────────────────────────────────────────
# 2a. Extract article via Lightpanda
# ─────────────────────────────────────────────
def _lightpanda_available() -> bool:
    """检查 lightpanda 命令是否可用。"""
    return shutil.which("lightpanda") is not None


def extract_via_lightpanda(url: str) -> Optional[dict]:
    """
    通过 Lightpanda headless 浏览器提取 Bloomberg 文章 __NEXT_DATA__。
    Lightpanda 是用 Zig 写的轻量 headless 浏览器，比 Chrome 快 ~9x，内存低 ~16x。
    """
    # 等待 __NEXT_DATA__ 就绪的 JS 表达式
    wait_expr = (
        "document.getElementById('__NEXT_DATA__') && "
        "document.getElementById('__NEXT_DATA__').textContent.length > 1000"
    )

    try:
        result = subprocess.run(
            [
                "lightpanda", "fetch", url,
                "--dump", "html",
                "--wait-ms", str(LIGHTPANDA_TIMEOUT * 1000),
                "--wait-script", wait_expr,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=LIGHTPANDA_TIMEOUT + 10,
        )
    except subprocess.TimeoutExpired:
        print("    ⚠️  Lightpanda 超时", file=sys.stderr)
        return None

    if result.returncode != 0:
        stderr_tail = result.stderr.strip()[-300:] if result.stderr else ""
        print(f"    ⚠️  Lightpanda 错误 (exit {result.returncode}): {stderr_tail}", file=sys.stderr)
        return None

    # 解析 JSON 输出，找到包含 content 的对象
    html_content = ""
    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if "content" in obj:
                html_content = obj["content"]
                break
        except json.JSONDecodeError:
            continue

    if not html_content:
        print("    ⚠️  Lightpanda 输出中未找到 HTML content", file=sys.stderr)
        return None

    return _parse_next_data(html_content)


def _parse_next_data(html_content: str) -> Optional[dict]:
    """从 HTML 中提取 __NEXT_DATA__ 并解析文章数据。"""
    idx = html_content.find("__NEXT_DATA__")
    if idx < 0:
        print("    ⚠️  页面中未找到 __NEXT_DATA__", file=sys.stderr)
        return None

    start = html_content.find(">", idx) + 1
    end = html_content.find("</script>", start)
    if start <= 0 or end < 0:
        print("    ⚠️  __NEXT_DATA__ script 标签解析失败", file=sys.stderr)
        return None

    try:
        next_data = json.loads(html_content[start:end].strip())
    except json.JSONDecodeError as e:
        print(f"    ⚠️  __NEXT_DATA__ JSON 解析失败: {e}", file=sys.stderr)
        return None

    story = next_data.get("props", {}).get("pageProps", {}).get("story")
    if not story:
        print("    ⚠️  __NEXT_DATA__ 中未找到 story", file=sys.stderr)
        return None

    ai_summary = ""
    ai = story.get("aiSummary")
    if ai and isinstance(ai, dict) and ai.get("text"):
        ai_summary = " ".join(ai["text"])

    body = story.get("body", {})
    if isinstance(body, list):
        nodes = body
    elif isinstance(body, dict):
        nodes = body.get("content", [])
    else:
        nodes = []

    return {
        "title": story.get("headline", ""),
        "byline": story.get("byline", ""),
        "published": story.get("publishedAt", ""),
        "ai_summary": ai_summary,
        "body_nodes": nodes,
    }


# ─────────────────────────────────────────────
# 2b. Extract article via Chrome AppleScript (fallback)
# ─────────────────────────────────────────────
def extract_via_chrome(url: str) -> Optional[dict]:
    """
    通过 Chrome AppleScript 打开 Bloomberg 文章并提取 __NEXT_DATA__。
    Bloomberg 反爬严格（curl/headless 均被 403），只能用真实浏览器。
    """
    # 构造 AppleScript：打开 URL → 等待 __NEXT_DATA__ → 提取元数据 + 正文 JSON
    script = f'''
tell application "Google Chrome"
    activate

    -- 找一个可用的窗口
    if (count of windows) = 0 then
        make new window
    end if
    set targetWindow to front window

    -- 打开新标签页
    set newTab to make new tab at end of tabs of targetWindow
    set URL of newTab to "{url}"

    -- 等待页面加载
    delay 8

    -- 轮询等待 __NEXT_DATA__ 就绪
    set waitCount to 0
    repeat while waitCount < 30
        set nextDataLen to execute newTab javascript "var nd = document.getElementById('__NEXT_DATA__'); nd ? String(nd.textContent.length) : '0';"
        try
            if (nextDataLen as integer) > 1000 then exit repeat
        end try
        delay 1
        set waitCount to waitCount + 1
    end repeat

    -- 提取元数据
    set metaJSON to execute newTab javascript "
        try {{
            var txt = document.getElementById('__NEXT_DATA__').textContent;
            var j = JSON.parse(txt);
            var s = j.props.pageProps.story;
            var summary = '';
            if (s.aiSummary && s.aiSummary.text) {{
                summary = s.aiSummary.text.join(' ');
            }}
            JSON.stringify({{
                headline: s.headline || '',
                byline: s.byline || '',
                publishedAt: s.publishedAt || '',
                aiSummary: summary
            }});
        }} catch(e) {{
            JSON.stringify({{error: e.message}});
        }}
    "

    -- 提取正文 JSON（大文本需要分块）
    set bodyJSON to execute newTab javascript "
        try {{
            var txt = document.getElementById('__NEXT_DATA__').textContent;
            var j = JSON.parse(txt);
            var s = j.props.pageProps.story;
            if (s && s.body) {{
                JSON.stringify(s.body);
            }} else {{
                JSON.stringify({{error: 'no body'}});
            }}
        }} catch(e) {{
            JSON.stringify({{error: e.message}});
        }}
    "

    return metaJSON & "|||" & bodyJSON
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=CHROME_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print("    ⚠️  Chrome AppleScript 超时", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"    ⚠️  AppleScript 错误: {result.stderr[:200]}", file=sys.stderr)
        return None

    output = result.stdout.strip()
    parts = output.split("|||", 1)
    if len(parts) != 2:
        print(f"    ⚠️  输出格式异常: {output[:200]}", file=sys.stderr)
        return None

    try:
        meta = json.loads(parts[0])
    except json.JSONDecodeError:
        print(f"    ⚠️  元数据解析失败: {parts[0][:200]}", file=sys.stderr)
        return None

    if "error" in meta:
        print(f"    ⚠️  元数据错误: {meta['error']}", file=sys.stderr)
        return None

    try:
        body = json.loads(parts[1])
    except json.JSONDecodeError:
        print(f"    ⚠️  正文解析失败", file=sys.stderr)
        return None

    if isinstance(body, dict) and "error" in body:
        print(f"    ⚠️  正文错误: {body['error']}", file=sys.stderr)
        return None

    # body 可能是 array（旧格式）或 document 对象（新格式，内容在 body.content 中）
    if isinstance(body, list):
        nodes = body
    elif isinstance(body, dict):
        nodes = body.get("content", [])
    else:
        nodes = []

    return {
        "title": meta.get("headline", ""),
        "byline": meta.get("byline", ""),
        "published": meta.get("publishedAt", ""),
        "ai_summary": meta.get("aiSummary", ""),
        "body_nodes": nodes,
    }


# ─────────────────────────────────────────────
# 2c. Unified extract dispatcher
# ─────────────────────────────────────────────
def extract_article(url: str, backend: str = "auto") -> Optional[dict]:
    """统一提取接口：根据 backend 选择 lightpanda 或 chrome。"""
    if backend == "chrome":
        return extract_via_chrome(url)
    # auto 或 lightpanda：优先 lightpanda，不可用时 fallback 到 chrome
    if _lightpanda_available():
        return extract_via_lightpanda(url)
    print("    ℹ️  Lightpanda 不可用，fallback 到 Chrome AppleScript")
    return extract_via_chrome(url)



# ─────────────────────────────────────────────
# 3a. Translation via Google Translate (free)
# ─────────────────────────────────────────────
_TRANSLATE_CACHE: dict[str, str] = {}


def translate_text(text: str, src: str = "en", tgt: str = "zh-CN") -> str:
    """翻译文本。使用 Google Translate 免费端点，带缓存。"""
    if not text or not text.strip():
        return text
    cache_key = f"{src}:{tgt}:{text}"
    if cache_key in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[cache_key]
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": src, "tl": tgt, "dt": "t", "q": text},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        result = "".join(seg[0] for seg in data[0])
        _TRANSLATE_CACHE[cache_key] = result
        return result
    except Exception as e:
        print(f"    ⚠️  翻译失败: {e}", file=sys.stderr)
        return text


def _fix_image_url(url: str) -> str:
    """修复图片 URL：webp → jpg，移除动态图表 URL。"""
    if not url:
        return url
    # Bloomberg 的 webp 图片在邮件客户端不显示，换成 jpg
    # /v0/-1x-1.webp → /v0/1200x-1.jpg
    if url.endswith(".webp"):
        url = re.sub(r"/[^/]+.webp$", "/1200x-1.jpg", url)
    # toaster/v2/charts 是动态图表，邮件里无法渲染，返回空
    if "toaster/v2/charts" in url:
        return ""
    return url


def translate_article(article: dict) -> dict:
    """翻译文章标题、作者、AI 摘要和正文中的文本节点。"""
    if article.get("title"):
        article["title"] = translate_text(article["title"])
    if article.get("ai_summary"):
        article["ai_summary"] = translate_text(article["ai_summary"])
    # 翻译正文节点（递归）
    if article.get("body_nodes"):
        article["body_nodes"] = [_translate_node(n) for n in article["body_nodes"]]
    # 修复图片
    if article.get("image"):
        article["image"] = _fix_image_url(article["image"])
    return article


def _translate_node(n: dict) -> dict:
    """递归翻译文本节点。"""
    if not n:
        return n
    t = n.get("type", "")
    if t == "text":
        value = n.get("value", "")
        if value.strip():
            n["value"] = translate_text(value)
        return n
    if n.get("content"):
        n["content"] = [_translate_node(c) for c in n["content"]]
    return n

# ─────────────────────────────────────────────
# 3b. Render body nodes → HTML
# ─────────────────────────────────────────────
def esc(s: str) -> str:
    return html_module.escape(s or "")


def render_node(n: dict) -> str:
    """递归渲染 Bloomberg body JSON 节点为 HTML。"""
    if not n:
        return ""
    t = n.get("type", "")

    if t == "text":
        return esc(n.get("value", ""))

    if t == "link":
        inner = "".join(render_node(c) for c in n.get("content", []))
        href = n.get("data", {}).get("href", "")
        if not href:
            return inner
        if href.startswith("http"):
            return f'<a href="{esc(href)}" style="color:#0969da">{inner}</a>'
        title = n.get("data", {}).get("title", "")
        if href.startswith("bbg://") and title:
            search = f"https://www.bloomberg.com/search?query={requests.utils.quote(title)}"
            return f'<a href="{esc(search)}" style="color:#666;font-style:italic">{inner}</a>'
        return inner

    if t == "entity":
        inner = "".join(render_node(c) for c in n.get("content", []))
        if n.get("subType") == "person":
            return f"<strong>{inner}</strong>"
        link = n.get("data", {}).get("link", {})
        if link:
            web_url = link.get("data-web-url") or link.get("webUrl") or ""
            if not web_url:
                web_url = link.get("destination", {}).get("web", "")
            if web_url:
                return f'<a href="{esc(web_url)}" style="color:#0969da">{inner}</a>'
            lt = link.get("title", "")
            if lt:
                search = f"https://www.bloomberg.com/search?query={requests.utils.quote(lt)}"
                return f'<a href="{esc(search)}" style="color:#0969da">{inner}</a>'
        return inner

    if t == "paragraph":
        inner = "".join(render_node(c) for c in n.get("content", []))
        return f'<p style="margin:0.8em 0;line-height:1.7;font-size:15px">{inner}</p>'

    if t == "heading":
        lvl = n.get("data", {}).get("level", 2)
        inner = "".join(render_node(c) for c in n.get("content", []))
        sizes = {1: "24px", 2: "20px", 3: "17px"}
        sz = sizes.get(lvl, "17px")
        return f'<h{lvl} style="font-size:{sz};font-weight:bold;margin:1.2em 0 0.5em">{inner}</h{lvl}>'

    if t == "list":
        tag = "ol" if n.get("subType") == "ordered" else "ul"
        inner = "".join(render_node(c) for c in n.get("content", []))
        return f'<{tag} style="margin:0.8em 0;padding-left:1.5em">{inner}</{tag}>'

    if t == "listItem":
        return "<li>" + "".join(render_node(c) for c in n.get("content", [])) + "</li>"

    if t == "media" and n.get("subType") == "chart":
        data = n.get("data", {})
        chart = data.get("chart", {})
        att = data.get("attachment", {})
        src = chart.get("src", "")
        if not src:
            return ""
        c_title = att.get("title", "")
        c_foot = att.get("footnote", "")
        c_src = att.get("source", "")
        h = [f'<div style="margin:1.2em 0;padding:12px;background:#f8f9fa;border-radius:4px">']
        fixed_src = _fix_image_url(src)
        if fixed_src:
            h.append(f'<img src="{esc(fixed_src)}" style="max-width:100%;height:auto">')
        if c_title:
            h.append(f'<p style="font-size:13px;font-weight:bold;margin:8px 0 0">{esc(c_title)}</p>')
        if c_foot:
            h.append(f'<p style="font-size:11px;color:#666;margin:4px 0 0">{esc(c_foot)}</p>')
        if c_src:
            h.append(f'<p style="font-size:11px;color:#999;margin:2px 0 0">Source: {esc(c_src)}</p>')
        h.append("</div>")
        return "".join(h)

    if t in ("media", "ad", "inline-newsletter"):
        return ""

    if n.get("content"):
        return "".join(render_node(c) for c in n["content"])
    return ""


def render_article_html(article: dict, index: int) -> str:
    """将单篇文章渲染为 HTML 块。"""
    title = esc(article.get("title", "Untitled"))
    byline = esc(article.get("byline", ""))
    published = article.get("published", "")
    ai_summary = article.get("ai_summary", "")
    body_nodes = article.get("body_nodes", [])
    url = article.get("url", "")
    image = article.get("image", "")

    if published:
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            pub_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, TypeError):
            pub_str = published[:19]
    else:
        pub_str = ""

    h = []
    h.append(f'<div id="article-{index}" style="margin-bottom:32px">')

    if image:
        h.append(f'<img src="{esc(image)}" style="width:100%;max-height:400px;object-fit:cover;border-radius:4px;margin-bottom:12px">')

    h.append(f'<h2 style="font-size:22px;line-height:1.3;margin:0 0 6px">{index}. {title}</h2>')

    meta = []
    if byline:
        meta.append(f"<strong>{byline}</strong>")
    if pub_str:
        meta.append(pub_str)
    if url:
        meta.append(f'<a href="{esc(url)}" style="color:#0969da">原文链接</a>')
    if meta:
        h.append(f'<p style="font-size:12px;color:#666;margin:0 0 12px">{"&nbsp;|&nbsp;".join(meta)}</p>')

    if ai_summary:
        h.append(
            f'<div style="padding:10px 14px;background:#fff8f0;border-left:4px solid #ff8c00;margin:0 0 16px;border-radius:0 4px 4px 0">'
            f'<p style="font-size:13px;font-weight:bold;margin:0 0 4px;color:#ff8c00">💡 AI Summary</p>'
            f'<p style="font-size:13px;line-height:1.6;margin:0;color:#333">{esc(ai_summary)}</p>'
            f'</div>'
        )

    body_html = "".join(render_node(n) for n in body_nodes)
    if body_html:
        h.append(body_html)
    elif not body_nodes:
        h.append(f'<p style="color:#999;font-style:italic">（正文提取失败，请点击原文链接查看）</p>')

    h.append("</div>")
    return "\n".join(h)


def build_email_html(articles: list[dict]) -> str:
    """将多篇文章包装成 HTML 邮件。"""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>',
        '<body style="margin:0;padding:20px;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif">',
        f'<div style="max-width:720px;margin:0 auto;background:#fff;padding:24px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.1)">',
        f'<div style="border-bottom:3px solid #000;padding-bottom:12px;margin-bottom:24px">',
        f'<h1 style="font-size:20px;margin:0">📰 Bloomberg Daily Headlines</h1>',
        f'<p style="font-size:12px;color:#999;margin:4px 0 0">{now_str} · {len(articles)} articles</p>',
        f'</div>',
    ]

    # 目录导航
    parts.append('<div style="margin-bottom:24px;padding:16px;background:#f0f4f8;border-radius:6px">')
    parts.append('<p style="font-size:15px;font-weight:bold;margin:0 0 10px">📋 目录</p>')
    for ti, ta in enumerate(articles):
        t_title = ta.get('title', f'Article {ti+1}')
        parts.append(f'<p style="margin:4px 0;font-size:14px;line-height:1.6">'
                     f'<a href="#article-{ti+1}" style="color:#0969da;text-decoration:none">'
                     f'{ti+1}. {esc(t_title)}</a></p>')
    parts.append('</div>')

    for i, article in enumerate(articles):
        if i > 0:
            parts.append('<hr style="border:none;border-top:1px solid #eee;margin:28px 0">')
        parts.append(render_article_html(article, i + 1))

    parts.append("</div></body></html>")
    return "\n".join(parts)


def build_email_plain(articles: list[dict]) -> str:
    """纯文本 fallback。"""
    lines = [f"Bloomberg Daily Headlines ({len(articles)} articles)", "=" * 50, ""]
    for i, a in enumerate(articles):
        lines.append(f"{i+1}. {a.get('title', 'Untitled')}")
        if a.get("byline"):
            lines.append(f"   By {a['byline']}")
        if a.get("url"):
            lines.append(f"   {a['url']}")
        if a.get("ai_summary"):
            lines.append(f"   Summary: {a['ai_summary']}")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 4. Send email
# ─────────────────────────────────────────────
def send_email(
    to_addr: str,
    subject: str,
    html_body: str,
    plain_body: str,
    smtp_host: str = "",
    smtp_port: int = 465,
    smtp_user: str = "",
    smtp_pass: str = "",
) -> bool:
    """发送 HTML 邮件。优先 SMTP，否则通过 aio.home 的 Postfix 投递。"""
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user or "bloomberg@aio.home"
    msg["To"] = to_addr
    msg.attach(email.mime.text.MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))

    if smtp_host and smtp_user and smtp_pass:
        # 外部 SMTP（QQ 邮箱等）
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=30) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            print(f"  ✅ 邮件已通过 SMTP ({smtp_host}) 发送到 {to_addr}")
            return True
        except Exception as e:
            print(f"  ❌ SMTP 发送失败: {e}", file=sys.stderr)
            return False

    # 通过 aio.home 的 Postfix 投递（家用 IP 直连 MX 会被拒，aio 有合法域名）
    try:
        payload = msg.as_string()
        proc = subprocess.run(
            ["ssh", "aio", "/usr/sbin/sendmail", "-t"],
            input=payload, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            print(f"  ✅ 邮件已通过 aio.home 投递到 {to_addr}")
            return True
        else:
            print(f"  ❌ aio sendmail 失败: {proc.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"  ❌ aio sendmail 异常: {e}", file=sys.stderr)

    # 本机 sendmail fallback
    try:
        proc = subprocess.run(
            ["/usr/sbin/sendmail", "-t"],
            input=msg.as_string(), capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            print(f"  ✅ 邮件已通过本机 sendmail 投递到 {to_addr}")
            return True
        else:
            print(f"  ❌ sendmail 失败 (exit {proc.returncode}): {proc.stderr[:200]}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"  ❌ sendmail 异常: {e}", file=sys.stderr)
        return False


# ─────────────────────────────────────────────
# 5. Main
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Bloomberg Headline Grabber — 抓取 Bloomberg 头条并发送邮件")
    parser.add_argument("--to", default=DEFAULT_TO, help=f"收件人邮箱 (默认: {DEFAULT_TO})")
    parser.add_argument("--count", type=int, default=5, help="抓取文章数量 (默认: 5)")
    parser.add_argument("--url", help="抓取指定 URL（忽略 sitemap 列表）")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不发送邮件")
    parser.add_argument("--smtp-host", default=os.getenv("BLOOMBERG_SMTP_HOST", ""))
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("BLOOMBERG_SMTP_PORT", "465")))
    parser.add_argument("--smtp-user", default=os.getenv("BLOOMBERG_SMTP_USER", ""))
    parser.add_argument("--smtp-pass", default=os.getenv("BLOOMBERG_SMTP_PASS", ""))
    parser.add_argument("--output", "-o", help="保存 HTML 到文件")
    parser.add_argument("--skip-extract", action="store_true", help="只获取标题列表，不提取正文")
    parser.add_argument("--source", choices=["homepage", "sitemap"], default="homepage",
                        help="头条来源: homepage=首页编辑精选(默认), sitemap=最新发布")
    parser.add_argument("--backend", choices=["auto", "lightpanda", "chrome"], default="auto",
                        help="正文提取后端 (默认: auto，优先 lightpanda)")
    args = parser.parse_args()

    print("📰 Bloomberg Headline Grabber")
    print("=" * 50)

    # Step 1: 获取文章列表
    if args.url:
        articles = [{"title": "", "url": args.url, "published": "", "image": ""}]
        print(f"\n📌 指定文章: {args.url}")
    else:
        print(f"\n🔍 获取最新 {args.count} 条头条 (source={args.source})...")
        try:
            articles = fetch_headlines(args.count, source=args.source)
        except Exception as e:
            print(f"❌ 获取头条失败: {e}", file=sys.stderr)
            sys.exit(1)

        if not articles:
            print("❌ 没有找到任何文章", file=sys.stderr)
            sys.exit(1)

        for i, a in enumerate(articles):
            pub_short = a["published"][:16] if a["published"] else ""
            print(f"  {i+1}. [{pub_short}] {a['title']}")

    # Step 2: 提取正文
    if not args.skip_extract:
        # 确定实际使用的后端
        actual_backend = args.backend
        if actual_backend == "auto":
            if _lightpanda_available():
                actual_backend = "lightpanda"
            else:
                actual_backend = "chrome"
        print(f"\n📖 通过 {actual_backend.upper()} 提取 {len(articles)} 篇文章正文...")

        # Chrome 后端需要确保浏览器在运行
        if actual_backend == "chrome":
            chrome_check = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to (name of processes) contains "Google Chrome"'],
                capture_output=True, text=True, timeout=5,
            )
            if "true" not in chrome_check.stdout.lower():
                print("\n🚀 启动 Google Chrome...")
                subprocess.run(["open", "-a", "Google Chrome"], timeout=10)
                time.sleep(3)

        for i, article in enumerate(articles):
            print(f"\n  [{i+1}/{len(articles)}] {article['title'][:60]}...")
            result = extract_article(article["url"], backend=actual_backend)
            if result:
                article.update({
                    "title": result["title"] or article.get("title", ""),
                    "byline": result["byline"],
                    "published": result["published"] or article.get("published", ""),
                    "ai_summary": result["ai_summary"],
                    "body_nodes": result["body_nodes"],
                })
                node_count = len(result["body_nodes"])
                print(f"          ✅ 成功: {node_count} 个内容节点")
            else:
                article.setdefault("body_nodes", [])
                article.setdefault("byline", "")
                article.setdefault("ai_summary", "")
                print(f"          ⚠️  失败，跳过正文")

            if i < len(articles) - 1:
                time.sleep(2)
    else:
        for a in articles:
            a.setdefault("body_nodes", [])
            a.setdefault("byline", "")
            a.setdefault("ai_summary", "")

    # Step 2.5: 翻译 + 修复图片
    if not args.skip_extract:
        print(f"\n🌐 翻译为中文 + 修复图片...")
        for i, article in enumerate(articles):
            translate_article(article)
            print(f"  [{i+1}/{len(articles)}] {article.get('title', '')[:50]}")

    # Step 3: 生成邮件
    now = datetime.now()
    subject = f"📰 Bloomberg 头条 — {now.strftime('%Y-%m-%d')}"
    html_body = build_email_html(articles)
    plain_body = build_email_plain(articles)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"\n💾 HTML 已保存到: {args.output}")

    # Step 4: 发送
    if args.dry_run:
        preview = "/tmp/bloomberg_preview.html"
        with open(preview, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"\n🏷️  Dry Run — 邮件预览:")
        print(f"  Subject: {subject}")
        print(f"  To: {args.to}")
        print(f"  Articles: {len(articles)}")
        print(f"  HTML: {len(html_body)} chars")
        print(f"  Preview: {preview}")
    else:
        print(f"\n📤 发送邮件到 {args.to}...")
        ok = send_email(
            to_addr=args.to,
            subject=subject,
            html_body=html_body,
            plain_body=plain_body,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user,
            smtp_pass=args.smtp_pass,
        )
        if not ok:
            print("\n💡 提示: 请配置 SMTP 参数发送邮件：")
            print("  --smtp-host smtp.qq.com --smtp-port 465")
            print("  --smtp-user YOUR@qq.com --smtp-pass YOUR_AUTH_CODE")
            print("  或设置环境变量: BLOOMBERG_SMTP_HOST, BLOOMBERG_SMTP_USER, BLOOMBERG_SMTP_PASS")
            sys.exit(1)

    print("\n✅ 完成！")


if __name__ == "__main__":
    main()
