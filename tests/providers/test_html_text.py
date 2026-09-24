from apply_agent.providers.html_text import html_to_text


def test_keeps_visible_text_and_paragraph_breaks() -> None:
    html = "<html><body><p>Dear Sam,</p><p>We regret to <b>inform</b> you.</p></body></html>"
    assert html_to_text(html) == "Dear Sam,\n\nWe regret to inform you."


def test_drops_scripts_styles_and_head() -> None:
    html = (
        "<html><head><title>T</title><style>p{color:red}</style></head>"
        "<body><script>track()</script><p>Visible</p></body></html>"
    )
    assert html_to_text(html) == "Visible"


def test_decodes_entities_and_collapses_whitespace() -> None:
    assert html_to_text("<p>Lumen &amp;   Vale&nbsp;Ltd</p>") == "Lumen & Vale\xa0Ltd"


def test_line_breaks_and_table_rows_become_newlines() -> None:
    assert html_to_text("a<br>b<table><tr><td>c</td></tr></table>") == "a\nb\n\nc"
