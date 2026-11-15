"""pdf_rooms.image_outlines: tags of a vector page outlined on its render (the model is replaced by a stub)."""
from jstore.frames import PT_M
from jstore.interpret import pdf_rooms as P

K = PT_M * 48                     # 1/4" = 1'-0"
S = 1.25                          # render pixels per point


def tag(label, x, y, m2=None, strong=True, known=True, num=None):
    return dict(label=label, labels=[], num=num, number=None, area=dict(val=(m2, "ft2", m2 / P.SQFT), text="x") if m2 else None,
                strong=strong, known=known, bbox=[x - 20, y - 5, x + 20, y + 5], x=x, y=y, poly=None)


def square_px(x, y, side_m, k=K):
    h = side_m / k / 2 * S
    cx, cy = x * S, y * S
    return [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]


def stub(sides):
    def outline(asks, m_per_px):
        out = []
        for a, side in zip(asks, sides):
            b = a["label_box"]
            out.append(dict(polygon=None, reason="no answer") if side is None else
                       dict(polygon=square_px((b[0] + b[2]) / 2 / S, (b[1] + b[3]) / 2 / S, side), support=0.9,
                            label_inside=True, others_inside=0, touches_crop_border=False, overlap=0.0))
        return out
    return outline


PAGE = dict(page_no=0, width=2448.0, height=1584.0, image="renders/x/p0.png", to_image=[S, 0, 0, 0, S, 0])


def test_printed_areas_choose_the_printed_scale_and_outlines_come_back_in_points():
    tags = [tag("BEDROOM", 400, 400, 16.0), tag("OFFICE", 900, 400, 9.0), tag("UTILITY", 400, 900, 25.0),
            tag("KITCHEN", 900, 900, strong=False)]
    info = {}
    k = P.image_outlines(PAGE, tags, info, None, [PT_M * 4, K], 100, outline=stub([4.0, 3.05, 5.0]))
    assert abs(k - K) < 1e-9 and "confirmed by printed areas" in info["scale"]
    assert [t["poly"] is not None for t in tags] == [True, True, True, False]      # the weak tag is not asked without a scale
    assert abs(tags[0]["poly"].area * k * k - 16.0) < 0.01 and tags[0]["how"] == "image_outline"
    assert tags[0]["conf"] == P.IMAGE_CONF["agrees_with_printed"]
    assert abs(tags[0]["poly"].centroid.x - 400) < 0.01                            # page points, not pixels
    assert info["image_outline"]["kept"] == 3 and info["image_outline"]["within_10pct_of_printed"] == 3


def test_an_outline_that_contradicts_the_printed_area_is_dropped_and_weak_tags_need_a_plausible_size():
    tags = [tag("BEDROOM", 400, 400, 16.0), tag("OFFICE", 900, 400, 9.0), tag("KITCHEN", 900, 900, strong=False),
            tag("HALL", 400, 900, strong=False), tag("BATH", 1400, 400, 5.0)]
    info = {}
    k = P.image_outlines(PAGE, tags, info, K, [K], 100, outline=stub([4.0, 6.0, 3.0, 0.5, None]))
    assert k == K
    assert [t["poly"] is not None for t in tags] == [True, False, True, False, False]
    assert "another size than printed" in tags[1]["rejected"] and "not confirmed" in tags[3]["rejected"]
    assert "no outline on the page render" in tags[4]["rejected"]
    assert tags[2]["conf"] == P.IMAGE_CONF["plain"]


def test_nothing_is_asked_without_budget_or_render():
    tags = [tag("BEDROOM", 400, 400, 16.0)]
    assert P.image_outlines(PAGE, tags, {}, K, [K], 5, outline=stub([4.0])) == K and tags[0]["poly"] is None
    assert P.image_outlines(dict(PAGE, image=None), tags, {}, K, [K], 100, outline=stub([4.0])) == K and tags[0]["poly"] is None


def test_printed_areas_that_agree_on_another_scale_replace_a_stated_scale():
    tags = [tag("BEDROOM", 400, 400, 16.0), tag("OFFICE", 900, 400, 9.0), tag("UTILITY", 400, 900, 25.0)]
    info = dict(scale="frame (scale_text)")
    k = P.image_outlines(PAGE, tags, info, K * 2, [K * 2], 100, outline=stub([4.0, 3.0, 5.0]))
    assert abs(k - K) < 1e-6 and "contradict" in info["scale"] and all(t["poly"] is not None for t in tags)
