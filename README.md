# Bloomberg Grabber 📰

抓取 Bloomberg 最新头条新闻，提取完整正文（绕过 paywall），并通过邮件发送。

## 工作原理

1. **获取头条列表** — 从 Bloomberg 的 Google News Sitemap 获取最新文章（标题/URL/时间/头图），无需认证
2. **提取完整正文** — 通过 macOS Chrome AppleScript 操作真实浏览器，从 `__NEXT_DATA__` JSON 提取完整文章
3. **渲染 HTML** — 解析递归文档树，保留链接、图表、AI Summary
4. **发送邮件** — 通过本机 Postfix（macOS 自带）直接投递，**无需 SMTP 授权码**

## 使用方法

```bash
# 抓取前 5 条头条并发送到默认邮箱
python3 bloomberg_grabber.py

# 抓取 10 条
python3 bloomberg_grabber.py --count 10

# 指定收件人
python3 bloomberg_grabber.py --to someone@example.com

# 只预览不发邮件
python3 bloomberg_grabber.py --count 3 --dry-run

# 只获取标题列表（不需要 Chrome）
python3 bloomberg_grabber.py --skip-extract --count 20

# 保存 HTML 到文件
python3 bloomberg_grabber.py --count 3 --output headlines.html

# 抓取指定文章
python3 bloomberg_grabber.py --url https://www.bloomberg.com/news/articles/...
```

## 邮件发送

默认通过 macOS 本机 Postfix 直接投递（和 aio.home/ds.home 一样），**不需要 SMTP 授权码**。

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
- macOS + Google Chrome（正文提取需要）

## 定时执行

```bash
# 每天早上 9 点抓取 5 条头条发到邮箱
0 9 * * * cd /Users/weiwang/Projects/bloomberg-grabber && python3 bloomberg_grabber.py --count 5
```
