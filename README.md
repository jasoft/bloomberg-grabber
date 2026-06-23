# Bloomberg Grabber 📰

抓取 Bloomberg 最新头条新闻，提取完整正文（绕过 paywall），并通过邮件发送。

## 工作原理

1. **获取头条列表** — 从 Bloomberg 的 Google News Sitemap 获取最新文章（标题/URL/时间/头图），无需认证
2. **提取完整正文** — 通过 [Lightpanda](https://github.com/lightpanda-io/browser) headless 浏览器从 `__NEXT_DATA__` JSON 提取完整文章（默认）
   - Lightpanda 是用 Zig 写的轻量 headless 浏览器，比 Chrome 快 ~9x，内存低 ~16x
   - 也支持通过 macOS Chrome AppleScript 提取（`--backend chrome`）
3. **渲染 HTML** — 解析递归文档树，保留链接、图表、AI Summary
4. **发送邮件** — 通过本机 Postfix / aio.home Postfix 投递，或 SMTP 发送

## 使用方法

```bash
# 抓取前 5 条头条并发送到默认邮箱（使用 Lightpanda）
python3 bloomberg_grabber.py

# 抓取 10 条
python3 bloomberg_grabber.py --count 10

# 指定收件人
python3 bloomberg_grabber.py --to someone@example.com

# 只预览不发邮件
python3 bloomberg_grabber.py --count 3 --dry-run

# 只获取标题列表（不需要浏览器）
python3 bloomberg_grabber.py --skip-extract --count 20

# 保存 HTML 到文件
python3 bloomberg_grabber.py --count 3 --output headlines.html

# 抓取指定文章
python3 bloomberg_grabber.py --url https://www.bloomberg.com/news/articles/...

# 强制使用 Chrome AppleScript（需要打开 Chrome）
python3 bloomberg_grabber.py --backend chrome --count 3
```

## 后端选择

| 后端 | 说明 | 优点 | 缺点 |
|------|------|------|------|
| `lightpanda`（默认） | Lightpanda headless 浏览器 | 快速、无需 GUI、低内存 | 需要安装 lightpanda |
| `chrome` | macOS Chrome AppleScript | 使用真实浏览器 | 需要打开 Chrome、速度慢 |

自动选择逻辑：如果 lightpanda 可用则优先使用，否则 fallback 到 Chrome。

### 安装 Lightpanda

```bash
# Homebrew（推荐）
brew install lightpanda-io/browser/lightpanda

# 或直接下载
curl -L -o /usr/local/bin/lightpanda https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-aarch64-macos && chmod a+x /usr/local/bin/lightpanda
```

## 邮件发送

默认通过 macOS 本机 Postfix / aio.home Postfix 投递，**不需要 SMTP 授权码**。

如果需要通过外部 SMTP 发送（如 QQ 邮箱 SMTP）：

```bash
export BLOOMBERG_SMTP_HOST=smtp.qq.com
export BLOOMBERG_SMTP_PORT=465
export BLOOMBERG_SMTP_USER=your@qq.com
export BLOOMBERG_SMTP_PASS=your_auth_code
python3 bloomberg_grabber.py
```

## 依赖

- Python 3.9+
- `requests` (`pip3 install requests`)
- [Lightpanda](https://github.com/lightpanda-io/browser)（推荐，`brew install lightpanda-io/browser/lightpanda`）
- macOS + Google Chrome（仅 `--backend chrome` 时需要）

## 定时执行

```bash
# 每天早上 9 点抓取 5 条头条发到邮箱
0 9 * * * cd /Users/weiwang/Projects/bloomberg-grabber && python3 bloomberg_grabber.py --count 5
```
