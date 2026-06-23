# Bloomberg Grabber 📰

抓取 Bloomberg 最新头条新闻，提取完整正文（绕过 paywall），并通过邮件发送。

## 工作原理

1. **获取头条列表** — 从 Bloomberg 的 Google News Sitemap (`/feeds/sitemap_news.xml`) 获取最新文章，包含标题、URL、发布时间、头图。无需认证。

2. **提取完整正文** — 通过 macOS Chrome AppleScript 操作真实浏览器标签页，从页面 `__NEXT_DATA__` JSON 中提取完整的结构化文章正文。
   - Bloomberg 反爬严格：curl 返回 403，headless 浏览器返回验证码页
   - 只有真实浏览器才能加载完整内容
   - `__NEXT_DATA__` 是 Next.js 注入的 JSON，包含完整文章数据（~137KB），即使 paywall 只显示 2 段预览

3. **渲染 HTML** — 解析递归 JSON 文档树（text/link/entity/paragraph/media 等节点类型），保留链接、图表、AI Summary

4. **发送邮件** — 通过 SMTP 或 macOS sendmail 发送 HTML 邮件

## 使用方法

```bash
# 基本用法：抓取前 5 条头条并发送到默认邮箱
python3 bloomberg_grabber.py

# 抓取指定数量
python3 bloomberg_grabber.py --count 10

# 指定收件人
python3 bloomberg_grabber.py --to someone@example.com

# 只预览不发送
python3 bloomberg_grabber.py --count 3 --dry-run

# 抓取指定文章
python3 bloomberg_grabber.py --url https://www.bloomberg.com/news/articles/... --to you@email.com

# 只获取标题列表（不需要 Chrome）
python3 bloomberg_grabber.py --skip-extract --count 20

# 保存 HTML 到文件
python3 bloomberg_grabber.py --count 3 --output headlines.html

# 使用 SMTP 发送（推荐）
python3 bloomberg_grabber.py \
  --smtp-host smtp.qq.com \
  --smtp-port 465 \
  --smtp-user YOUR@qq.com \
  --smtp-pass YOUR_AUTH_CODE \
  --to 1188540@qq.com
```

## SMTP 配置

### QQ 邮箱

1. 登录 QQ 邮箱 → 设置 → 账户 → 开启 SMTP 服务
2. 获取授权码（不是 QQ 密码）
3. 使用：
   ```bash
   --smtp-host smtp.qq.com --smtp-port 465 --smtp-user YOUR@qq.com --smtp-pass AUTH_CODE
   ```

### 环境变量

可以设置环境变量避免每次输入：

```bash
export BLOOMBERG_SMTP_HOST=smtp.qq.com
export BLOOMBERG_SMTP_PORT=465
export BLOOMBERG_SMTP_USER=your@qq.com
export BLOOMBERG_SMTP_PASS=your_auth_code
```

## 依赖

- Python 3.9+
- `requests` (`pip3 install requests`)
- macOS + Google Chrome（正文提取需要）

## 定时执行

可以用 cron 定时抓取：

```bash
# 每天早上 9 点抓取 5 条头条
0 9 * * * cd /Users/weiwang/Projects/bloomberg-grabber && python3 bloomberg_grabber.py --count 5
```

## 技术细节

详见 [docs/bloomberg-unpaywall-2026-06-23.md](../shell/docs/bloomberg-unpaywall-2026-06-23.md)（来自原始逆向工程文档）。
