from dev_memory_common import classify_content


def test_classifier_routes_known_semantic_kinds():
    assert classify_content("结论：统一使用 repo 级队列锁") == "decision"
    assert classify_content("风险：并发归档可能重复移动目录") == "risk"
    assert classify_content("术语：repo-key 指的是仓库身份摘要") == "glossary"


def test_classifier_keeps_ambiguous_content_unsorted_after_setup():
    content = "完成了这轮页面调整"

    assert classify_content(content, already_setup=False) == "unsorted"
    assert classify_content(content, already_setup=True) == "unsorted"
