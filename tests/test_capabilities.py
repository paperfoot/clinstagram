from clinstagram.backends.capabilities import GRAPH_ONLY_FEATURES, Feature, can_backend_do


def test_graph_ig_can_post():
    assert can_backend_do("graph_ig", Feature.POST_PHOTO)
    assert can_backend_do("graph_ig", Feature.POST_VIDEO)
    assert can_backend_do("graph_ig", Feature.POST_REEL)


def test_graph_ig_cannot_dm():
    assert not can_backend_do("graph_ig", Feature.DM_INBOX)
    assert not can_backend_do("graph_ig", Feature.DM_SEND_MEDIA)


def test_graph_fb_can_dm():
    assert can_backend_do("graph_fb", Feature.DM_INBOX)
    assert can_backend_do("graph_fb", Feature.DM_REPLY)


def test_graph_fb_cannot_cold_dm():
    assert not can_backend_do("graph_fb", Feature.DM_COLD_SEND)


def test_private_can_everything_but_graph_only_features():
    assert Feature.ANALYTICS_SUMMARY in GRAPH_ONLY_FEATURES
    for feat in Feature:
        assert can_backend_do("private", feat) == (feat not in GRAPH_ONLY_FEATURES), feat


def test_graph_only_features_run_on_graph():
    for feat in GRAPH_ONLY_FEATURES:
        assert can_backend_do("graph_ig", feat) or can_backend_do("graph_fb", feat), feat


def test_feature_enum_completeness():
    assert Feature.POST_PHOTO in Feature
    assert Feature.DM_INBOX in Feature
    assert Feature.STORY_POST in Feature
    assert Feature.FOLLOW in Feature
    assert Feature.ANALYTICS_PROFILE in Feature
