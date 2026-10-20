"""自动分类:按标题/UP主里的关键词给收藏视频分组。

为什么用规则而不是模型:

- 收藏夹只有几十上百条,规则足够准,而且**完全离线**、零依赖;
- 分类结果可解释——你点开就知道为什么归到这一类;
- 想改分类只要改下面的词表,不用训练/下载任何东西。

打分规则:标题命中权重最高,UP主次之,intro 最低;取总分最高的类别。
一分都没有就归到「其他」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Video

# 标题命中的权重:标题最说明内容,UP主和简介只是补充
TITLE_WEIGHT = 3
UPPER_WEIGHT = 2
INTRO_WEIGHT = 1
# 至少要有这么多分才算命中,避免一个词碰巧出现在简介里就归错类
MIN_SCORE = 2


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    keywords: tuple[str, ...]


# 顺序即默认展示顺序(同类里条数多的会排前面,见 group())
TOPICS: tuple[Topic, ...] = (
    Topic(
        "code",
        "编程开发",
        (
            "python", "java", "c++", "golang", "rust", "javascript", "typescript",
            "编程", "代码", "写代码", "算法", "数据结构", "前端", "后端", "全栈",
            "架构", "设计模式", "重构", "调试", "bug", "git", "linux", "docker",
            "数据库", "sql", "框架", "爬虫", "接口", "函数", "变量", "环境变量",
        ),
    ),
    Topic(
        "ai",
        "AI / 机器学习",
        (
            "ai", "人工智能", "机器学习", "深度学习", "神经网络", "大模型", "llm",
            "pytorch", "tensorflow", "transformers", "训练模型", "推理", "微调",
            "chatgpt", "stable diffusion", "扩散模型", "nlp", "计算机视觉",
        ),
    ),
    Topic(
        "math",
        "数学",
        (
            "数学", "高等数学", "高数", "线性代数", "线代", "概率论", "数理统计",
            "微积分", "考研数学", "矩阵", "秩", "极限", "导数", "积分", "泰勒",
            "向量", "方程", "几何", "离散数学",
        ),
    ),
    Topic(
        "english",
        "英语 / 外刊",
        (
            "英语", "外刊", "双语", "精读", "泛读", "词汇", "语法", "雅思", "托福",
            "四级", "六级", "cet", "考研英语", "翻译", "口语", "听力", "经济学人",
            "the guardian", "bbc", "cnn", "english",
        ),
    ),
    Topic(
        "graphics",
        "图形学 / 渲染",
        (
            "图形学", "渲染", "光栅化", "着色器", "shader", "opengl", "vulkan",
            "directx", "unity", "unreal", "游戏引擎", "建模", "pbr", "光线追踪",
            "ray tracing", "计算机图形",
        ),
    ),
    Topic(
        "course",
        "公开课 / 大学课程",
        (
            "公开课", "哈佛", "mit", "斯坦福", "伯克利", "cs50", "导论", "课程",
            "慕课", "计算机科学", "操作系统", "编译原理", "计算机网络", "组成原理",
            "讲座", "lecture",
        ),
    ),
    Topic(
        "science",
        "科普 / 杂谈",
        (
            "科普", "为什么", "原理", "揭秘", "冷知识", "物理", "化学", "生物",
            "天文", "宇宙", "历史", "哲学", "思考", "解读",
        ),
    ),
)

OTHER_ID = "other"
OTHER_NAME = "其他"

_PUNCTUATION = re.compile(r"[\s\-_/\\|·、,，.。:：;；!！?？()（）\[\]【】\"'“”‘’]+")


def normalize(text: str) -> str:
    """把文本压成"小写、去标点、单空格"的形式,方便关键词包含匹配。"""
    return _PUNCTUATION.sub(" ", (text or "").lower()).strip()


def score_video(video: Video) -> tuple[str, dict[str, int]]:
    """返回 (目标类别 id, 各类得分)。得分表方便测试和排查。"""
    title = normalize(video.title)
    upper = normalize(video.upper)
    intro = normalize(getattr(video, "intro", "") or "")
    scores: dict[str, int] = {}
    for topic in TOPICS:
        total = 0
        for keyword in topic.keywords:
            needle = normalize(keyword)
            if not needle:
                continue
            if needle in title:
                total += TITLE_WEIGHT
            if needle in upper:
                total += UPPER_WEIGHT
            if needle and needle in intro:
                total += INTRO_WEIGHT
        if total:
            scores[topic.id] = total

    if not scores:
        return OTHER_ID, scores
    best = max(scores.items(), key=lambda item: (item[1], -_order_of(item[0])))
    if best[1] < MIN_SCORE:
        return OTHER_ID, scores
    return best[0], scores


def _order_of(topic_id: str) -> int:
    for index, topic in enumerate(TOPICS):
        if topic.id == topic_id:
            return index
    return len(TOPICS)


def name_of(topic_id: str) -> str:
    for topic in TOPICS:
        if topic.id == topic_id:
            return topic.name
    return OTHER_NAME


@dataclass
class Group:
    id: str
    name: str
    videos: list[Video] = field(default_factory=list)


def is_valid_topic(topic_id: str) -> bool:
    return topic_id == OTHER_ID or any(topic.id == topic_id for topic in TOPICS)


def all_topics() -> list["Topic"]:
    """界面上可选的分类(含「其他」)。"""
    return list(TOPICS) + [Topic(id=OTHER_ID, name=name_of(OTHER_ID), keywords=())]


def group(videos: list[Video], overrides: dict[str, str] | None = None) -> list[Group]:
    """把视频按类别分组,顺序:条数多的类别在前,「其他」永远最后。

    `overrides` 是用户手动纠正过的分类({视频 key: 分类 id}),优先级高于关键词打分。
    """
    forced = overrides or {}
    buckets: dict[str, list[Video]] = {}
    for video in videos:
        topic_id = forced.get(video.key)
        if not topic_id or not is_valid_topic(topic_id):
            topic_id, _scores = score_video(video)
        buckets.setdefault(topic_id, []).append(video)

    groups = [Group(id=topic_id, name=name_of(topic_id), videos=items) for topic_id, items in buckets.items()]
    groups.sort(
        key=lambda item: (
            item.id == OTHER_ID,      # 「其他」最后
            -len(item.videos),
            _order_of(item.id),
        )
    )
    return groups


def classify(video: Video) -> str:
    """单条视频的类别 id(测试和调试用)。"""
    return score_video(video)[0]
