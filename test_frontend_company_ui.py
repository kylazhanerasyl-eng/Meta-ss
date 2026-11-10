"""Регрессионные проверки интерфейса: кабинет партнёра, статусы, мобильные таблицы."""
import re

import pytest


@pytest.fixture(scope="module")
def html():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


def test_company_dashboard_present_and_wired(html):
    assert 'id="companySection"' in html
    assert "async function loadCompanyDashboard()" in html
    assert "async function submitCompanyTopic(ev)" in html
    assert "currentUser.role !== 'company'" in html
    assert "if (currentUser.role === 'company') loadCompanyDashboard();" in html
    # Вход партнёра не должен подписываться как «Студент».
    assert "company: { ru: 'Партнёр'" in html


@pytest.mark.parametrize("key", ["coSubmit", "coNoBids", "coPublishError", "statusAllocatedShort", "statusRejectedShort", "landingTagline"])
def test_new_texts_exist_in_all_languages(html, key):
    assert len(re.findall(rf"\b{key}:\s*\"", html)) == 3


def test_no_leftover_legacy_palette(html):
    for legacy in ("#B81140", "#D81A4E", "#16040F", "#FAF5F7"):
        assert legacy not in html.upper()


def test_reset_demo_button_not_shown_in_working_ui(html):
    assert 'id="headerResetDemoBtn"' not in html
    assert 'onclick="resetDemoData()"' not in html


def test_wide_tables_scroll_instead_of_collapsing(html):
    assert ".overflow-x-auto > table { min-width: 760px; }" in html


def test_brand_is_meta(html):
    assert '<h1 class="ml-title">META</h1>' in html
