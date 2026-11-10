import json
import re
import pytest


@pytest.fixture(scope="module")
def index_html_content():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def parsed_dict(index_html_content):
    # Extract the `const dict = { ... };` object from JS
    match = re.search(r"const\s+dict\s*=\s*(\{.*?\n\s*\});", index_html_content, re.DOTALL)
    assert match is not None, "dict object must exist in index.html"
    dict_js = match.group(1)
    
    ru_block = re.search(r"ru:\s*\{(.*?)\n\s*\},", dict_js, re.DOTALL).group(1)
    kz_block = re.search(r"kz:\s*\{(.*?)\n\s*\},", dict_js, re.DOTALL).group(1)
    en_block = re.search(r"en:\s*\{(.*?)\n\s*\}", dict_js, re.DOTALL).group(1)

    def parse_block(blk):
        res = {}
        for line in blk.splitlines():
            m = re.search(r'([a-zA-Z0-9_]+)\s*:\s*["\'](.*?)["\']', line)
            if m:
                res[m.group(1)] = m.group(2)
        return res

    return {
        "ru": parse_block(ru_block),
        "kz": parse_block(kz_block),
        "en": parse_block(en_block),
    }


def test_translations_decree_block_keys_exist_in_all_languages(parsed_dict):
    """
    Test 1.1: Verify decree block keys exist across RU, KZ, and EN.
    - Официальный приказ МОН РК
    - Код верификации
    - Реестр кафедры META
    - Контрольная сумма SHA-256
    """
    required_keys = [
        "orderOfficialTitle",
        "orderVerificationCode",
        "orderRegistryMeta",
        "orderSha256",
        "orderYear",
        "orderStatusVerified",
        "orderStatusFormed",
    ]

    for lang in ["ru", "kz", "en"]:
        for key in required_keys:
            assert key in parsed_dict[lang], f"Missing key '{key}' in language '{lang}'"
            val = parsed_dict[lang][key]
            assert len(val.strip()) > 0, f"Empty value for '{key}' in '{lang}'"

    # Specific assertions for the 4 user-requested decree terms
    # RU
    assert parsed_dict["ru"]["orderOfficialTitle"] == "Официальный приказ МОН РК"
    assert "Код верификации" in parsed_dict["ru"]["orderVerificationCode"]
    assert parsed_dict["ru"]["orderRegistryMeta"] in ["Реестр кафедры META", "Цифровой реестр ASTRA"]
    assert "Контрольная сумма SHA-256" in parsed_dict["ru"]["orderSha256"]

    # KZ
    assert parsed_dict["kz"]["orderOfficialTitle"] == "ҚР ҒЖБМ ресми бұйрығы"
    assert "Тексеру коды" in parsed_dict["kz"]["orderVerificationCode"]
    assert parsed_dict["kz"]["orderRegistryMeta"] in ["META кафедрасының тізілімі", "ASTRA цифрлық тізілімі"]
    assert "SHA-256 бақылау сомасы" in parsed_dict["kz"]["orderSha256"]

    # EN
    assert parsed_dict["en"]["orderOfficialTitle"] == "Official Ministry Decree"
    assert "Verification Code" in parsed_dict["en"]["orderVerificationCode"]
    assert parsed_dict["en"]["orderRegistryMeta"] in ["META Department Registry", "ASTRA Digital Registry"]
    assert "SHA-256 Checksum" in parsed_dict["en"]["orderSha256"]


def test_translations_head_dashboard_keys(parsed_dict):
    """
    Test 1.2: Verify Head Dashboard keys exist across RU, KZ, EN.
    """
    head_keys = [
        "headDeptTitle",
        "headMonitoringTitle",
        "btnRunGaleShapley",
        "btnConfirmAll",
        "btnDownloadDocx",
        "btnDownloadPdf",
        "btnVerifyOnline",
        "btnDownloadBackup",
        "headRegistryTitle",
        "recordsCount",
        "noApplicationsInRegistry",
        "statusConfirmedByHead",
        "thStudent",
        "thTopic",
        "thSupervisor",
        "thPriority",
        "thStatus",
        "gsModalTitle",
        "gsModalDesc",
        "gsStatStudents",
        "gsStatAllocated",
        "gsStatUnallocated",
        "gsNoPairs",
        "gsAssignedStatus",
        "btnCommitGaleShapley",
    ]

    for lang in ["ru", "kz", "en"]:
        for k in head_keys:
            assert k in parsed_dict[lang], f"Missing key '{k}' in language '{lang}'"
            assert len(parsed_dict[lang][k]) > 0


def test_html_decree_block_has_i18n_hooks(index_html_content):
    """
    Test 1.3: Verify Decree elements in index.html have data-i18n attributes or IDs.
    """
    assert 'id="headOrderInfoBar"' in index_html_content
    assert 'id="headOrderOfficialTitle"' in index_html_content
    assert 'data-i18n="orderOfficialTitle"' in index_html_content
    assert 'id="headOrderCodeLabel"' in index_html_content
    assert 'data-i18n="orderVerificationCode"' in index_html_content
    assert 'id="headOrderRegistryLabel"' in index_html_content
    assert 'data-i18n="orderRegistryMeta"' in index_html_content
    assert 'id="headOrderSha256Label"' in index_html_content
    assert 'data-i18n="orderSha256"' in index_html_content
    assert 'id="headOrderHashBadge"' in index_html_content
    assert 'id="headOrderYearBadge"' in index_html_content
    assert 'data-i18n="orderYear"' in index_html_content


def test_websocket_architecture_and_reconnect_loop_protection(index_html_content):
    """
    Test 2: Verify WebSocket architecture protects against reconnect loops.
    """
    # 1. Singleton state tracker exists
    assert "let currentWsUserId = null;" in index_html_content
    assert "let isWsManualClose = false;" in index_html_content

    # 2. closeWebSocket function cleanly detaches event handlers before closing
    assert "function closeWebSocket()" in index_html_content
    assert "wsConnection.onclose = null;" in index_html_content
    assert "clearTimeout(wsReconnectTimer);" in index_html_content

    # 3. connectWebSocket checks for active connection
    assert "if (wsConnection && currentWsUserId === userId)" in index_html_content
    assert "WebSocket.OPEN" in index_html_content
    assert "WebSocket.CONNECTING" in index_html_content

    # 4. onclose rejects reconnect on manual close and normal closure (1000)
    assert "if (isWsManualClose) return;" in index_html_content
    assert "code === 1000" in index_html_content

    # 5. Capped reconnect attempts and exponential backoff
    assert "wsReconnectAttempts >= 5" in index_html_content
    assert "Math.pow(2," in index_html_content

    # 6. updateUserInterface guards against reconnecting if already open
    assert "currentWsUserId !== currentUser.id" in index_html_content

    # 7. logout explicitly calls closeWebSocket
    assert "closeWebSocket();" in index_html_content


def test_f5_persistence_and_session_restore(index_html_content):
    """
    Test 3: Verify F5 persistence for meta_user, meta_token, meta_lang, meta_theme.
    """
    # Anti-FOUC theme script in head
    assert "localStorage.getItem('meta_theme')" in index_html_content

    # DOMContentLoaded initialization
    assert "initTheme();" in index_html_content
    assert "currentUser = JSON.parse(savedUser);" in index_html_content
    assert "setLanguage(currentLang);" in index_html_content

    # Backend verification with logout fallback on 401
    assert "apiFetch('/api/auth/me')" in index_html_content
    assert "logout(true);" in index_html_content
