# 📰 morning-news — 全球视野，中国洞察

短视频在收割注意力，营销号在消解信息。算法推荐让你看到的不是世界，而是你想看到的幻象。

**morning-news 不为了好看而存在。**

它是一个自托管的新闻聚合 Agent，每日自动从 X/Twitter、百度热搜、Hacker News、Reddit、Nature/Science RSS 抓取原始新闻，经 LLM 去重、分类、核实来源，输出一份干净、客观、有结构的全球简报。

---

## 为什么做这个

信息过载的时代，输入比消化容易得多。打开任何 App，你看到的都是精心编排的情绪，不是事实。

morning-news 的核心理念：
- **不看热度看事实**——不依赖算法推荐，只看原始信源
- **拒绝营销号**——过滤广告、推广、AI 生成的垃圾内容
- **多源交叉验证**——同一件事，看 X 怎么看、Reddit 怎么说、国内怎么报
- **来源透明**——每条新闻标注出处（CNN / Reuters / @verified 账号），不虚构

这不是一个 App，这是一个你可以在服务器上自己跑、自己改、完全掌控的新闻 Agent。

## 架构

```
morning-news/
├── scripts/
│   ├── fetch_news.py         # 核心抓取脚本
│   └── bird-search/          # X/Twitter 搜索工具（MIT License）
│       ├── bird-search.mjs
│       ├── lib/              # X API 客户端库
│       └── package.json
├── references/
│   └── cron_prompt.md        # cron 任务格式化指令
├── SKILL.md                  # Hermes Agent Skill 配置说明
└── README.md                 # 就是你看到的这个
```

## 数据源

脚本抓取 3 大类共 7 个数据源，每日更新：

### 🌍 国际新闻

| 来源 | 方式 | 代理 | 说明 |
|---|---|---|---|
| **X/Twitter** | bird-search (Node.js) | 需要 | 全球大事的第一现场，按关键词搜索最新推文 |
| **Hacker News** | Algolia API | 需要 | 硅谷/科技圈的热议话题 |
| **Reddit** | reddit.com/r/*/hot.json | 需要 | 世界网民的多角度讨论 |

### 🇨🇳 国内热点

| 来源 | 方式 | 代理 | 说明 |
|---|---|---|---|
| **百度热搜** | top.baidu.com API | **不需要** | 国内娱乐、体育、社会热点 TOP50（无需 Cookie） |
| **iTHome** | RSS Feed | **不需要** | 国内 IT 资讯 |

### 🔬 科研进展

| 来源 | 方式 | 代理 | 说明 |
|---|---|---|---|
| **Nature** | RSS | 需要 | 顶级科学期刊最新论文 |
| **Science Daily** | RSS | 需要 | 每日科学突破报道 |
| **Science.org** | RSS | 需要 | 综合性科研新闻 |

## 输出结构

脚本生成结构化 JSON，包含四大板块：

```json
{
  "date": "2026-04-26",
  "today_news": {
    "ai": [{ "source": "x", "title": "...", "first_line": "...", ... }],
    "politics": [...],
    "tech": [...],
    "general": [...]
  },
  "hot_topics": [...],
  "baidu_hot": [{ "title": "...", "desc": "...", "hot_score": 0 }],
  "science": [...],
  "china": [...]
}
```

由 LLM（配合 Hermes Agent cron 任务）读取后格式化为可读的 Markdown 早报。

## 快速上手

### 前置条件

- Python 3.8+
- Node.js 18+
- （中国服务器）HTTP 代理，如 `http://127.0.0.1:7890`
- （可选）X/Twitter Cookie，用于搜索 X

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/XiuFan719/morning-news.git
cd morning-news

# 2. 安装 X 搜索依赖
cd scripts/bird-search && npm install && cd ../..

# 3. 配置 X Cookie（可选，不配则跳过 X 来源）
mkdir -p ~/.config/morning-news
cat > ~/.config/morning-news/.env << 'EOF'
AUTH_TOKEN=你的auth_token
CT0=你的ct0
EOF
chmod 600 ~/.config/morning-news/.env

# 4. 运行
export PATH="$HOME/local/node/bin:$PATH"
export http_proxy="http://127.0.0.1:7890"
export https_proxy="http://127.0.0.1:7890"
python3 scripts/fetch_news.py > /tmp/briefing.json

# 5. 查看结果
python3 -m json.tool /tmp/briefing.json | head -40
```

### 配合 Hermes Agent 定时推送

如果你使用 [Hermes Agent](https://github.com/XiuFan719/hermes-agent)（或任何 LLM Agent），可以设置每日 cron 自动格式化并推送早报：

```bash
hermes cronjob create \
  --name "DargonNews" \
  --schedule "30 7 * * *" \
  --toolsets terminal \
  --prompt "$(cat references/cron_prompt.md)"
```

也可直接读取 JSON 自行集成到任何通知系统（Telegram、微信、邮件等）。

## 自定义

### 修改 X 搜索关键词

编辑 `scripts/fetch_news.py` 中的 `X_QUERIES` 列表：

```python
X_QUERIES = [
    ("Trump OR Biden OR US politics OR breaking news", "politics", 20),
    ("war OR military OR conflict OR missile OR strike", "politics", 15),
    ("China OR Taiwan OR South China Sea OR Philippines", "politics", 12),
    ("AI OR artificial intelligence OR LLM OR GPT", "ai", 10),
    # 在这里添加你自己的关键词
]
```

每个元组格式：`(搜索关键词, 分类, 抓取条数)`

### 添加 Reddit 子版块

```python
REDDIT_SUBREDDITS = [
    "worldnews", "technology", "politics", "science",
    # 添加更多子版块
]
```

### 修改分类规则

脚本内置政治/AI/科技/通用四类关键词列表，可直接编辑：
- `POLITICS_KEYWORDS`
- `AI_KEYWORDS`
- `TECH_KEYWORDS`
- `ENTERTAINMENT_KEYWORDS`



## 📄 每日论文推荐

每天早上自动从 arXiv（cs.AI/cs.CL/cs.LG/cs.CV）和 Hugging Face Daily Papers 抓取最新论文，**基于你的 Zotero 论文库做关键词匹配排序**，推荐与你研究方向最相关的 Top 5-8 篇论文。

### 需要配置

1. **Zotero 账号**（免费）— https://www.zotero.org
2. 获取你的 **User ID**（数字）和 **API Key**（Read Only）
3. 写入 `~/.config/morning-news/.env`：
```env
ZOTERO_USER_ID=你的数字ID
ZOTERO_API_KEY=你的只读API Key
```

### 工作原理

| 步骤 | 说明 |
|---|---|
| ① | 拉取 Zotero 论文库（标题+摘要），缓存 24h |
| ② | 从 arXiv 拉最新论文（4 个分类 × 20 篇） |
| ③ | 从 Hugging Face Daily Papers 拉取社区推荐论文 |
| ④ | 关键词匹配：Zotero 论文库的关键词与新论文交叉评分 |
| ⑤ | 按关联度排序，去重，输出 Top 8 |

```json
// 在 daily briefing JSON 中，papers 字段：
{
  "papers": [
    {
      "title": "PersonalAI: A Systematic Comparison...",
      "url": "https://huggingface.co/papers/2506.17001",
      "source": "HF_Daily",
      "relevance_score": 65,
      "matched_keywords": ["knowledge", "language", "models", ...]
    }
  ]
}
```

所有计算在本地完成，无需调用外部 LLM API。


## 输出示例

**🔥 持续热点**
- [X] BREAKING: President Trump shares CCTV footage of the alleged shooter at the White House Correspondents' Dinner

**🏛️ 国际政治/军事**
- [X] BREAKING: Trump, head table evacuated from White House Correspondents' Dinner

**🤖 AI 前沿**
- [HN] Google unveils way to train AI models across distributed data centers

**📄 今日论文推荐**
- 基于 Zotero 兴趣匹配的最新 arXiv 论文 Top 5
- 每天另附 Hugging Face 社区热门论文

**🔬 科研进展**
- [Nature] Cosmic-ray detection heralds era of mega-observatories for neutrinos

**📰 国内热点**
- 白宫记者晚宴发生枪击事件
- 东方甄选主播集体离职，俞敏洪回应
- 羽毛球取消21分制，改为15分制

## 许可证

MIT License。

`scripts/bird-search/` 目录源于 [@steipete/bird](https://github.com/steipete/bird) v0.8.0，MIT License，版权所有 Peter Steinberger。

## 声明

DargonNews 不存储、不分享任何用户数据。所有抓取内容仅用于生成个人简报。X Cookie 仅用于搜索，不用于任何其他操作。请遵守各平台的服务条款。
