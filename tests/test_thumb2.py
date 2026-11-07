from kirinuki import thumb2


def test_split_lines_natural_breaks():
    assert thumb2.split_lines("近すぎる敵") == ["近すぎる敵"]
    assert thumb2.split_lines("上手いのにへった") == ["上手いのに", "へった"]
    assert thumb2.split_lines("殺されるとも知らず") == ["殺されるとも", "知らず"]
    assert thumb2.split_lines("まさかのバンジョー") == ["まさかの", "バンジョー"]


def test_split_lines_small_kana_not_at_head():
    for a, b in [thumb2.split_lines(s) for s in ("早口すぎてはっや", "ちょっとやばいって")]:
        assert b[0] not in thumb2.NO_HEAD


def test_caption_one_line_two_layers_no_chat():
    ass = thumb2.caption_ass("殺されるとも知らず", "ARC Raiders", ("へった",))
    assert "殺されるとも知らず" in ass and "\\N" not in ass   # 1行に収める
    assert ass.count("殺されるとも知らず") == 2    # 黒縁の層と色文字の層
    assert "へった" not in ass                    # チャットの言葉は入れない
    main, _ = thumb2.pick_colors("殺されるとも知らず")
    assert main in ("&H001E1EF0", "&H00FFFFFF")   # 赤か白


def test_caption_long_text_splits():
    ass = thumb2.caption_ass("お菓子ティア表を作って全部語り尽くす男", "", ())
    assert "\\N" in ass


def test_split_lines_keeps_katakana_word():
    a, b = thumb2.split_lines("寿司ドームで爆笑")
    assert not (a.endswith("ー") and b.startswith("ム"))   # 「寿司ドー｜ム」にしない


def test_just_chatting_label_is_hidden():
    ass = thumb2.caption_ass("75万！？成金", "Just Chatting", ())
    assert "雑談" not in ass and "Just Chatting" not in ass


def test_usable_drops_dark_and_blurry():
    from kirinuki.thumb2 import usable
    assert usable([(120, 20), (20, 20), (120, 5)]) == [True, False, False]
    assert usable([(10, 1), (12, 1)]) == [True, True]   # 全部だめなら全部使う


def test_caption_emph_word_is_bigger():
    from kirinuki import thumb2
    ass = thumb2.caption_ass("本当にうれしい（泣）", "", emph="うれしい")
    line = [x for x in ass.splitlines() if x.startswith("Dialogue: 2") and "Cap," in x][0]
    import re
    sizes = [int(x) for x in re.findall(r"\\fs(\d+)", line)]
    assert sizes[1] == round(sizes[0] * thumb2.EMPH) and "うれしい" in line


def test_caption_emph_not_in_text_is_ignored():
    from kirinuki import thumb2
    assert thumb2.caption_ass("サスケお前さぁ", "", emph="ナルト") == thumb2.caption_ass("サスケお前さぁ", "")


def test_caption_emph_not_split_across_lines():
    from kirinuki import thumb2
    ass = thumb2.caption_ass("三田来いよ！ぶっ飛ばしてやる！", "", emph="ぶっ飛ばして")
    line = [x for x in ass.splitlines() if x.startswith("Dialogue: 2") and "Cap," in x][0]
    assert "ぶっ飛ばして" in line.split("\\N")[1]


def test_caption_badge_top_right():
    ass = thumb2.caption_ass("出ていきたくなる州", "ARC Raiders", badge="日本語字幕付き")
    assert "Style: Badge," in ass and "\\an9" in ass and "日本語字幕付き" in ass
    assert "日本語字幕付き" not in thumb2.caption_ass("出ていきたくなる州", "ARC Raiders")
