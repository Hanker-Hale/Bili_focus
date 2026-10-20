"""自动分类测试:关键词打分、分组顺序、边界情况。"""

from __future__ import annotations

import unittest

from favfocus import topics
from favfocus.models import Video


def make(bvid: str, title: str = "", upper: str = "u", intro: str = "") -> Video:
    return Video(aid=1, bvid=bvid, title=title, upper=upper, duration=600, fav_time=0, intro=intro)


class ClassifyTests(unittest.TestCase):
    def test_programming(self):
        self.assertEqual(topics.classify(make("BV1", "Python 编程入门:环境变量与函数")), "code")

    def test_math(self):
        self.assertEqual(topics.classify(make("BV2", "高等数学上册 750 题:函数极限")), "math")

    def test_english(self):
        self.assertEqual(topics.classify(make("BV3", "外刊精读 | 中英双语文稿")), "english")

    def test_ai(self):
        self.assertEqual(topics.classify(make("BV4", "一天学会 PyTorch 深度学习")), "ai")

    def test_graphics(self):
        self.assertEqual(topics.classify(make("BV5", "计算机图形学:光栅化与渲染")), "graphics")

    def test_course(self):
        self.assertEqual(topics.classify(make("BV6", "哈佛大学 CS50 公开课导论")), "course")

    def test_other_when_no_keyword(self):
        self.assertEqual(topics.classify(make("BV7", "我用爆火的 REA 逆向了 B 站")), "other")

    def test_title_beats_intro(self):
        # 标题讲数学,简介里提到英语:应归数学
        video = make("BV8", "矩阵的秩,一小时讲透", intro="英语学习资料附赠")
        self.assertEqual(topics.classify(video), "math")

    def test_upper_counts(self):
        video = make("BV9", "第一课", upper="考研数学名师")
        self.assertEqual(topics.classify(video), "math")

    def test_scores_are_explainable(self):
        topic_id, scores = topics.score_video(make("BV10", "外刊精读:经济学人"))
        self.assertEqual(topic_id, "english")
        self.assertIn("english", scores)
        self.assertGreater(scores["english"], 0)


class GroupTests(unittest.TestCase):
    def test_groups_put_same_category_together(self):
        videos = [
            make("BV1", "Python 编程入门"),
            make("BV2", "高等数学极限"),
            make("BV3", "Python 编程:函数"),
            make("BV4", "矩阵与线性代数"),
        ]
        groups = topics.group(videos)
        names = [group.name for group in groups]
        self.assertIn("编程开发", names)
        self.assertIn("数学", names)
        code_group = next(group for group in groups if group.name == "编程开发")
        self.assertEqual([video.bvid for video in code_group.videos], ["BV1", "BV3"])

    def test_other_is_last(self):
        videos = [
            make("BV1", "一个说不清的杂谈"),
            make("BV2", "Python 编程"),
            make("BV3", "另一个杂谈"),
        ]
        groups = topics.group(videos)
        self.assertEqual(groups[-1].id, "other")

    def test_larger_groups_first(self):
        videos = [
            make(f"BVM{i}", "高等数学 微积分") for i in range(3)
        ] + [make("BVC1", "Python 编程")]
        groups = topics.group(videos)
        self.assertEqual(groups[0].name, "数学")
        self.assertEqual(groups[1].name, "编程开发")

    def test_empty(self):
        self.assertEqual(topics.group([]), [])

    def test_every_video_kept_exactly_once(self):
        videos = [make(f"BV{i}", f"标题 {i} Python 编程") for i in range(10)]
        groups = topics.group(videos)
        total = sum(len(group.videos) for group in groups)
        self.assertEqual(total, 10)
        self.assertEqual(len({video.bvid for group in groups for video in group.videos}), 10)


class NormalizeTests(unittest.TestCase):
    def test_lowercases_and_strips_punctuation(self):
        self.assertEqual(topics.normalize("Python:入门/进阶!"), "python 入门 进阶")

    def test_empty(self):
        self.assertEqual(topics.normalize(""), "")


class OverrideTests(unittest.TestCase):
    """手动纠正的分类优先级高于关键词打分。"""

    def test_override_moves_video_to_chosen_group(self):
        video = make("BVt1", "随手拍的一段")
        auto = topics.group([video])
        self.assertEqual(auto[0].id, topics.OTHER_ID)

        forced = topics.group([video], {video.key: "graphics"})
        self.assertEqual(forced[0].id, "graphics")
        self.assertEqual(forced[0].name, "图形学 / 渲染")

    def test_override_to_other(self):
        video = make("BVt2", "Python 编程入门")
        forced = topics.group([video], {video.key: topics.OTHER_ID})
        self.assertEqual([group.id for group in forced], [topics.OTHER_ID])

    def test_invalid_override_falls_back_to_keywords(self):
        video = make("BVt2", "Python 编程入门")
        groups = topics.group([video], {video.key: "不存在的分类"})
        self.assertEqual(groups[0].id, "code")

    def test_unknown_topic_is_rejected(self):
        self.assertTrue(topics.is_valid_topic("code"))
        self.assertTrue(topics.is_valid_topic(topics.OTHER_ID))
        self.assertFalse(topics.is_valid_topic("nope"))

    def test_all_topics_includes_other_last(self):
        names = [topic.id for topic in topics.all_topics()]
        self.assertEqual(names[-1], topics.OTHER_ID)
        self.assertEqual(len(names), len(topics.TOPICS) + 1)


if __name__ == "__main__":
    unittest.main()
