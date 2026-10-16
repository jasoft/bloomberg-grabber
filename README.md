# Bloomberg Grabber 📰

抓取 Bloomberg 最新头条新闻，提取完整正文，翻译为中文，并通过邮件发送。

## 工作原理

1. **获取头条列表** — 通过 [Lightpanda](https://github.com/lightpanda-io/browser) 从 Bloomberg 首页 `__NEXT_DATA__` 提取编辑精选头条
2. **提取完整正文** — Lightpanda headless 浏览器从文章页 `__NEXT_DATA__` JSON 提取完整正文（失败自动重试 3 次）
3. **翻译为中文** — 通过 Google Translate 免费 API 自动翻译标题和正文
4. **渲染 HTML 邮件** — 生成带目录导航、可点击跳转的中文邮件
5. **发送邮件** — 通过 Postfix 投递或 SMTP 发送

## 安装

```bash
# Python 运行环境（依赖由 uv 自动隔离安装）
brew install uv

# Lightpanda headless 浏览器
brew install lightpanda-io/browser/lightpanda
```

## 配置

复制 `.env.example` 为 `.env` 并填入你的配置：

```bash
cp .env.example .env
```

| 变量 | 说明 | 必填 |
|------|------|------|
| `BLOOMBERG_TO` | 收件人邮箱 | ✅ |
| `BLOOMBERG_FROM` | 发件人地址 | ✅ |
| `BLOOMBERG_MAIL_HOST` | Postfix 投递的 SSH 主机 | ✅ |
| `BLOOMBERG_SMTP_HOST` | SMTP 服务器（可选） | ❌ |
| `BLOOMBERG_SMTP_PORT` | SMTP 端口 | ❌ |
| `BLOOMBERG_SMTP_USER` | SMTP 用户名 | ❌ |
| `BLOOMBERG_SMTP_PASS` | SMTP 密码 | ❌ |

## 使用方法

```bash
# 抓取前 5 条头条并发送到默认邮箱
uv run --script bloomberg_grabber.py

# 抓取 10 条
uv run --script bloomberg_grabber.py --count 10

# 指定收件人
uv run --script bloomberg_grabber.py --to someone@example.com

# 只预览不发邮件
uv run --script bloomberg_grabber.py --count 3 --dry-run

# 只获取标题列表（不提取正文）
uv run --script bloomberg_grabber.py --skip-extract --count 20

# 保存 HTML 到文件
uv run --script bloomberg_grabber.py --count 3 --output headlines.html

# 抓取指定文章
uv run --script bloomberg_grabber.py --url https://www.bloomberg.com/news/articles/...
```

## 定时执行

```bash
# 每天早上 9 点抓取 5 条头条发到邮箱
0 9 * * * cd /path/to/bloomberg-grabber && /opt/homebrew/bin/uv run --script bloomberg_grabber.py --count 5
```

macOS LaunchAgent 同样使用 `/opt/homebrew/bin/uv run --script /绝对路径/bloomberg_grabber.py --count 5`。
不要直接调用 Homebrew 的 `python3`：升级 Python 后，全局安装的依赖可能不再可用。

## 依赖

- Python 3.9+
- `uv`（根据脚本声明自动安装 `requests`）
- [Lightpanda](https://github.com/lightpanda-io/browser)

## License

MIT
