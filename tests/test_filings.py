from market_tracker import filings

PREV = """<html><body><p>Table of Contents</p><p>Item 1A. Risk Factors ..... 12</p><p>Item 1B. Unresolved Staff Comments ..... 30</p>
<p>Item 1A. Risk Factors</p>
<p>Our business depends on consumer demand for athletic footwear. Demand can fall in a recession and our sales would decline.</p>
<p>We rely on third-party manufacturers in Asia for most of our products and any disruption could hurt supply.</p>
<p>Our revenue was $51.2 billion in fiscal 2025 and we operate in more than 170 countries around the world.</p>
<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>
<p>Item 3. Legal Proceedings</p><p>We are party to ordinary course litigation that we do not expect to be material to the business.</p>
<p>Item 4. Mine Safety Disclosures</p></body></html>"""
CUR = PREV.replace("$51.2 billion in fiscal 2025", "$49.8 billion in fiscal 2026").replace(
    "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>",
    "<p>We identified a material weakness in our internal control over financial reporting related to inventory accounting.</p>"
    "<p>New tariffs on imports from Vietnam could raise our costs materially and reduce our gross margin next year.</p>"
    "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>")


def test_section_skips_the_table_of_contents():
    risk = filings.section(filings.html_to_text(CUR), "risk")
    assert risk.startswith("Our business depends") and "Unresolved" not in risk
    assert "ordinary course litigation" in filings.section(filings.html_to_text(CUR), "legal")


def test_compare_finds_new_sentences_and_ignores_new_numbers():
    prev, cur = filings.section(filings.html_to_text(PREV), "risk"), filings.section(filings.html_to_text(CUR), "risk")
    c = filings.compare(prev, cur)
    assert c["new_count"] == 2 and c["new"][0].startswith("We identified a material weakness")
    assert not any("fiscal 2026" in s for s in c["new"])            # only the numbers changed
    assert 0.3 < c["new_share"] < 0.6 and c["similarity"] < 1.0
    same = filings.compare(prev, prev)
    assert same["new_count"] == 0 and same["similarity"] == 1.0


def test_tenk_changes_end_to_end(monkeypatch):
    from market_tracker.providers import sec
    monkeypatch.setattr(sec, "ticker_map", lambda: type("M", (), {"cik_for": lambda self, t: "0000320187"})())
    subs = {"name": "NIKE, Inc.", "filings": {"recent": {
        "form": ["10-Q", "10-K", "8-K", "10-K"], "filingDate": ["2026-09-30", "2026-07-20", "2026-06-01", "2025-07-22"],
        "reportDate": ["2026-08-31", "2026-05-31", "", "2025-05-31"], "accessionNumber": ["a-1", "b-2", "c-3", "d-4"],
        "primaryDocument": ["q.htm", "k26.htm", "e.htm", "k25.htm"]}}}
    docs = {"k26.htm": filings.html_to_text(CUR), "k25.htm": filings.html_to_text(PREV)}
    r = filings.tenk_changes("NKE", get=lambda url, **k: subs, text_fn=lambda u: docs[u.rsplit("/", 1)[1]])
    assert r["current"]["filed"] == "2026-07-20" and r["previous"]["filed"] == "2025-07-22"
    assert r["level"] == "big" and r["sections"]["risk"]["new_count"] == 2
    assert r["sections"]["legal"]["new_count"] == 0


def test_light_edits_are_not_new_text():
    prev = ("The Company depends on component suppliers in Asia for most of its products and shipments. "
            "Changes in trade policy could raise the Company's costs and reduce its gross margin materially. "
            "The Company faces intense competition in every market in which it sells its hardware products.")
    cur = ("Apple depends on component suppliers in Asia for most of its products and shipments. "        # one word changed
           "Changes in trade policy, including new tariffs, could raise the Company's costs and reduce its gross margin materially. "
           "The Company faces intense competition in every market in which it sells its hardware products. "
           "A cybersecurity breach at a cloud provider could disrupt the Company's services for customers worldwide.")
    c = filings.compare(prev, cur)
    assert c["new_count"] == 1 and c["new"][0].startswith("A cybersecurity breach")
    assert c["removed_count"] == 0 and c["new_share"] < 0.35


def test_split_sentences_and_abbreviations_are_not_new():
    prev = ("The Company's products and services are offered in highly competitive global markets characterized by aggressive "
            "price competition, frequent introduction of new products, short product life cycles and evolving industry standards. "
            "A case is pending before the U.S. District Court for the District of Columbia (D.C. Circuit) about app store rules.")
    cur = ("The Company's products and services are offered in highly competitive global markets. "
           "These markets are characterized by aggressive price competition and frequent introduction of new products. "
           "A case is pending before the U.S. District Court for the District of Columbia (D.C. Circuit) about app store rules.")
    assert len(filings.sentences(cur)) == 3
    c = filings.compare(prev, cur)
    assert c["new_count"] == 0, c["new"]


def test_joined_sentences_and_word_forms_are_not_new():
    # From Apple's 2024 and 2025 Risk Factors: two old sentences joined, and "broader" -> "broad".
    prev = ("The Company has a minority market share in the global smartphone, personal computer and tablet markets. "
            "Some of the markets in which the Company competes have from time to time experienced little to no growth or contracted overall. "
            "In addition, some of the Company's competitors have broader product lines, lower-priced products and a larger installed base of active devices. "
            "The application of tax laws may be uncertain, require significant judgment and be subject to differing interpretations.")
    cur = ("The Company has a minority market share in the global smartphone, personal computer, tablet and wearables markets, and some "
           "of the markets in which the Company competes have from time to time experienced little to no growth or contracted overall. "
           "Some of the Company's competitors have broad product lines, low-priced products, large installed bases of active devices, and large customer bases. "
           "The court's order is subject to further proceedings before the D.C. District Court, which may result in changes to the "
           "interpretation or application of the remedies ordered by the court, as well as new or changed remedies being ordered. "
           "Beginning in the second quarter of 2025, new tariffs were announced on imports to the U.S., including additional tariffs on imports from China.")
    c = filings.compare(prev, cur)
    assert c["new_count"] == 2, c["new"]
    assert sorted(s[:9] for s in c["new"]) == ["Beginning", "The court"]


def test_small_caps_headings_join_but_other_tags_keep_words_apart():
    h = '<p>ITEM 1A. R<span style="font-size:8pt">ISK</span> F<font>ACTORS</font></p><p><span>We</span><span>face</span> <b>many</b>RISKS</p>'
    assert filings.html_to_text(h) == "ITEM 1A. RISK FACTORS \n We face many RISKS"
    lower = '<p>I<span style="font-variant:small-caps">tem</span> 1A. R<span style="font-variant:small-caps">isk</span> F<span>actors</span></p>'
    assert filings.html_to_text(lower) == "Item 1A. Risk Factors"


def test_a_late_cross_reference_is_not_the_section():
    body = "Real risk sentence here. " * 50
    text = ("Item 1A. Risk Factors .... 12\nItem 1B. Unresolved\nItem 1A. Risk Factors\n" + body + "\nItem 1B. Unresolved Staff Comments\n"
            + "Exhibits and more. " * 3000 + " as described in Item 1A. Risk Factors, and exhibits " + "Exhibit 10.1 agreement. " * 3000)
    assert filings.section(text, "risk") == body.strip()
