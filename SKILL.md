---
name: daily-news-briefing
version: "2.0.0"
description: "独立的多源新闻简报聚合器。从 X/Twitter、百度热搜、Hacker News、Reddit、Nature/Science RSS 抓取新闻，分类输出为结构化 JSON 供 LLM 格式化。完全自包含，不依赖其他 skill。"
tags: [news, briefing, x-twitter, baidu, hacker-news, reddit, rss, cron]
---

# Daily News Briefing

独立的多源新闻简报聚合器。从 X/Twitter、百度热搜、Hacker News、Reddit、Nature/Science Daily RSS 抓取新闻，分类并去重后输出结构化 JSON。完全自包含，不依赖其他 Hermes skill。

## 架构

```
daily_news_briefing/
├── scripts/
│   ├── fetch_news.py       # 主脚本（数据抓取 + 去重 + 分类 + 输出 JSON）
│   └── bird-search/        # X/Twitter 搜索工具（自包含 MIT，@steipete/bird）
├── SKILL.md
└── references/
    └── cron_prompt.md      # cron 任务 prompt 模板
```

## 依赖

- **Python 3.8+**（系统自带）
- **Node.js 18+**（X 搜索需要，本地安装或系统自带）
- **X cookie**（AUTH_TOKEN + CT0，从浏览器登录 x.com 后获取）
- **HTTP 代理**（中国服务器需要，如 `http://127.0.0.1:7890`）

## 安装

### 1. 拷贝 bird-search 并安装依赖

```bash
cp -r ~/.hermes/skills/news/daily-news-briefing/scripts/bird-search ~/.hermes/scripts/bird-search
cd ~/.hermes/scripts/bird-search && npm install
```

### 2. 配置 X cookie

```bash
mkdir -p ~/.config/daily-news-briefing
cat > ~/.config/daily-news-briefing/.env << 'EOF'
AUTH_TOKEN=your_auth_token_here
CT0=your_ct0_here
EOF
chmod 600 ~/.config/daily-news-briefing/.env
```

### 3. 拷贝主脚本并测试

```bash
cp ~/.hermes/skills/news/daily-news-briefing/scripts/fetch_news.py ~/.hermes/scripts/daily_x_briefing.py
export PATH="$HOME/local/node/bin:$PATH"
export http_proxy="http://127.0.0.1:7890"
export https_proxy="http://127.0.0.1:7890"
python3 ~/.hermes/scripts/daily_x_briefing.py > /tmp/briefing.json
```

### 4. 设置 cron 任务

```bash
hermes cronjob create \
  --name "每日早报" \
  --schedule "30 7 * * *" \
  --toolsets terminal \
  --prompt "$(cat ~/.hermes/skills/news/daily-news-briefing/references/cron_prompt.md)"
```

## 数据源

| 源 | 获取方式 | 是否需要代理 | 是否需要 Cookie |
|---|---|---|---|
| X/Twitter | bird-search (Node.js) | 是 | 是 (AUTH_TOKEN + CT0) |
| Hacker News | hn.algolia.com API | 是 | 否 |
| Reddit | reddit.com/r/*/hot.json | 是 | 否 |
| Nature/Science | RSS feeds | 是 | 否 |
| 百度热搜 | top.baidu.com API | **否** | **否** |
| iTHome | RSS feed | **否** | **否** |

### 百度热搜（国内娱乐/社会热点）说明

百度热搜 API 是国内最稳定的公开热点接口，**不需要任何 cookie 或代理**，直接返回 JSON。这是经过验证的最佳国内娱乐/社会热点源：

```
GET https://top.baidu.com/api/board?tab=realtime
```

返回结构：`data.cards[].content[]` 中每个 entry 包含：
- `word` — 热搜标题
- `desc` — 事件描述
- `hotScore` — 热度指数
- `url` — 相关链接

**最佳实践：** 用百度热搜替代 Reddit 娱乐子版块（r/funny/r/pics/r/videos），后者在中国环境下不稳定且内容不相关。

## 输出结构

```json
{
  "date": "2026-04-26",
  "total_raw": { "x": 36, "hn": 41, "reddit": 0, "science": 9, "china": 5, "baidu_hot": 50 },
  "today_news": {
    "ai": [{ "source": "x", "title": "...", "first_line": "...", "likes": 0, "author": "" }],
    "politics": [...],
    "tech": [...],
    "general": [...]
  },
  "hot_topics": [
    { "source": "x", "first_line": "...", "title": "...", "url": "...", "likes": 0 }
  ],
  "entertainment": [],
  "science": [{ "source": "nature", "title": "...", "url": "...", "desc": "..." }],
  "china": [{ "source": "ithome", "title": "...", "url": "..." }],
  "baidu_hot": [{ "title": "...", "desc": "...", "url": "...", "hot_score": 0 }]
}
```

### 三个关键字段

- **`today_news`** — 今日首次出现的新内容（由 seen-stories 去重判断），分 4 个类别
- **`hot_topics`** — 昨日已见过但仍在持续发酵的重大新闻（top 8 by engagement）
- **`baidu_hot`** — 国内热搜榜（独立于 seen-stories，每次都是最新的）

## 搜索关键词策略（核心经验）

**政治/军事关键词必须权重最高，且排在 AI/Tech 前面。** 这是用户最关心的内容。

正确的 X_QUERIES 配置：

```python
X_QUERIES = [
    ("Trump OR Biden OR US politics OR breaking news", "politics", 20),
    ("war OR military OR conflict OR missile OR strike", "politics", 15),
    ("China OR Taiwan OR South China Sea OR Philippines", "politics", 12),
    ("Russia OR Ukraine OR NATO OR Iran OR Israel", "politics", 12),
    ("politics OR geopolitics OR election OR trade tariff", "politics", 12),
    # AI/Tech
    ("AI OR artificial intelligence OR LLM OR GPT OR OpenAI", "ai", 10),
    ("technology OR tech startup OR chip OR semiconductor", "tech", 10),
    # Science/General
    ("science OR space OR NASA OR medical breakthrough", "general", 8),
    ("OpenAI OR Anthropic OR DeepSeek OR Claude OR Gemini", "ai", 8),
]
```

**经验：** 原配置 8 个 query 一半是 AI/Tech，导致政治新闻被淹没。政治 query 数量翻倍 + count 提高到 20 才能抓到足够多的地缘政治大事件。

## 分类优先级（防止误分类）

```python
def categorize_text(text, default_cat="general"):
    text_lower = text.lower()
    # 1. Politics FIRST — many political tweets mention "AI" in passing
    if any(k in text_lower for k in POLITICS_KEYWORDS):
        return "politics"
    # 2. AI second
    if any(k in text_lower for k in AI_KEYWORDS):
        return "ai"
    # 3. Tech third
    if any(k in text_lower for k in TECH_KEYWORDS):
        return "tech"
    # 4. Entertainment last
    if any(k in text_lower for k in ENTERTAINMENT_KEYWORDS):
        return "entertainment"
    return default_cat
```

**经验：** 如果 AI 放在 politics 前面，政治新闻里提到的 "AI weapons"、"AI intelligence report" 会被误分类为 AI。

## 去重机制

### 跨源模糊去重
```python
def dedup_items(items):
    # 标准化标题（去标点 + 小写 + 取前80字符）
    # 如果 short_title 是 long_title 的前缀，或 vice versa → 视为重复
    # 保留 engagement 更高的那条
```

### Seen-Stories 持久化
- 每天保存到 `~/.hermes/scripts/.seen_stories.json`
- `CRON_RUN=1` 环境变量控制是否持久化（手动测试时不保存，否则会"消耗"掉所有新内容）
- 保留 7 天，自动清理过期
- **坑：** 如果不设置 CRON_RUN，每次手动运行都会把当天故事标记为"已见"，但又不持久化，导致第二天 cron 认为所有内容都是新的

## cron prompt 模板

参考 `references/cron_prompt.md`。

## 版权说明

`bird-search/` 目录源于 [@steipete/bird](https://github.com/steipete/bird) v0.8.0（MIT License, Peter Steinberger）。仅包含搜索功能子集，已做代理适配（通过 https-proxy-agent 注入 http_proxy）。

## Pitfalls

1. **X cookie 过期**：AUTH_TOKEN/CT0 每隔几周需更新。症状是 X 返回 0 条数据。
2. **Reddit 可能返回 0 条**：hot 排序时间窗口短，不影响其他源。
3. **Science.org RSS 在 Python 3.8 上 SSL 报错**：已捕获，正常跳过。
4. **Node.js fetch 不读 http_proxy**：bird-search 已通过 https-proxy-agent 注入代理。如果 fork 这个工具，切记需要显式代理配置。
5. **CRON_RUN=1 必须设置**：否则 seen-stories 不会持久化，每天重复报道。
6. **MIN_X_LIKES 阈值**：设为 200 会遗漏早期突发事件（此时 engagement 还没涨起来）。建议 100。
7. **分类顺序**：Politics 必须在 AI 之前检查，否则政治新闻中提及 AI 会被误分。
8. **模型输出差异**：MiniMax 的输出偏正式（"引发广泛讨论"），DeepSeek 更直接。cron prompt 需要适配对应的模型风格。
9. **X cookie 应该从独立配置文件读取**，而不是依赖环境变量。脚本启动时自动从 `~/.config/daily-news-briefing/.env` 加载。
10. **百度热搜中的数据需要去重**（同一事件在热搜榜和置顶区都会出现），且在 cron prompt 中需告诉 LLM 区别"国际政治新闻"（如白宫枪击）和"国内娱乐"。
11. **Reddit 的娱乐子版块**（r/funny/r/pics/r/videos）在中国环境下不稳定，内容也与国内用户不相关。用百度热搜替代效果更好。
13. **娱乐板块数据**应标注来源（百度热搜 vs Reddit），国内热搜用 desc 字段做简介，国外娱乐用 Reddit 标题。

## cron prompt 设计经验

### 三大板块结构

最终验证有效的 cron prompt 把国内热点分为三个子板块：

1. **娱乐热搜** — 明星、综艺、影视、音乐（用百度热搜的 desc 作为简介）
2. **社会热点** — 民生新闻、社会事件、奇闻趣事（筛 5-6 条最有看点的）
3. **体育/其他** — 体育赛事、科技突破、不属于前两类的热门内容（筛 4-5 条）

### 关键规则

- **百度热搜中的政治新闻**（如"美国白宫记者晚宴发生枪击事件"）属于国际新闻，不放在国内板块
- **国内军事/国防新闻**（如"解放军全地形电动滑板车投入实战"）放在政治/军事板块
- **同一事件出现在多个源的**，在 hot_topics 集中描述进展，today_news 不再重复
- **X 来源需标注作者账号名**（@CNN、@Reuters），帮助用户判断可信度
- **百度热搜中带 desc 的条目**优先使用 desc 作为内容说明，比只列标题更有信息量

### 模型适配

The cron prompt template should be tailored to the model being used:
- **MiniMax**: 输出偏正式官方（"引发广泛讨论"、"彰显决心"），domestic 新闻会自动展开
- **DeepSeek**: 更直接中性，需要明确指示"科研详细报道"、"政治充分展开"
