"""Execute the real checkpoint download handlers with a mocked browser in Node.

No browser packages are needed. The API/DOM mocks check header and download
behavior; backend access enforcement remains covered by test_backend_audit.py.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


INDEX = Path(__file__).with_name("index.html")
NODE = shutil.which("node")

# Exercise the handler emitted by each real HTML template, not a Python replica
# of the implementation. The DOM only contains the APIs used by this download.
NODE_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = {
    requests: [], downloads: [], revoked: [], toasts: [],
    prevented: 0, logouts: [], modals: [], blobs: 0, removed: 0
};
let token = 'obsolete-session-token';
const context = {
    Headers, FormData,
    console: {warn() {}, error() {}},
    localStorage: {getItem: name => name === 'meta_token' ? token : null},
    logout: expired => calls.logouts.push(expired),
    openAuthModal: mode => calls.modals.push(mode),
    showToast: (...args) => calls.toasts.push(args),
    window: {URL: {
        createObjectURL: blob => {
            if (blob !== 'protected-binary-data') throw Error('unexpected blob');
            return 'blob:authenticated-file';
        },
        revokeObjectURL: url => calls.revoked.push(url)
    }},
    document: {
        body: {appendChild() {}},
        createElement: name => {
            if (name !== 'a') throw Error('unexpected element');
            return {
                click() {calls.downloads.push({href: this.href, name: this.download});},
                remove() {calls.removed++;}
            };
        }
    },
    fetch: async (url, options) => {
        calls.requests.push({url, headers: options.headers});
        if (input.networkError) throw Error('offline');
        return {
            ok: input.status >= 200 && input.status < 300,
            status: input.status,
            headers: new Headers(input.disposition ? {'Content-Disposition': input.disposition} : {}),
            blob: async () => {calls.blobs++; return 'protected-binary-data';},
            clone: () => ({json: async () => ({detail: 'Access denied'})})
        };
    }
};
vm.createContext(context);
vm.runInContext(input.escapeSource + '\n' + input.helperSource, context);
context.c = {id: 7, download_url: '/api/checkpoints/download/7', file_name: input.fileName};
const rendered = vm.runInContext('`' + input.anchor + '`', context);
const decode = text => text.replace(/&(amp|lt|gt|quot|#39);/g,
    (_, name) => ({amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'"})[name]);
const attrs = {};
for (const match of rendered.matchAll(/([\w-]+)="([^"]*)"/g)) attrs[match[1]] = decode(match[2]);
context.link = {dataset: {downloadUrl: attrs['data-download-url'], downloadName: attrs['data-download-name']}};
context.clickEvent = {preventDefault() {calls.prevented++;}};
// A renewed session must be read when clicked, without caching the old token.
token = input.token;
(async () => {
    await vm.runInContext('(function(event) {' + attrs.onclick + '}).call(link, clickEvent)', context);
    process.stdout.write(JSON.stringify({calls, rendered, attrs}));
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


REVISION_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = { opened: [], subtitle: null };
const context = {
    globalThis: {},
    document: {
        getElementById: id => {
            if (id === 'checkpointReviewModal') return { classList: { remove() {}, add() {} }, style: {} };
            if (id === 'reviewModalCheckpointId') return { value: '' };
            if (id === 'reviewModalSubtitle') return {
                set textContent(v) { calls.subtitle = v; }
            };
            if (id === 'reviewModalComment') return { value: '' };
            return null;
        }
    },
    openReviewRevisionModal: (...args) => calls.opened.push(args)
};
vm.createContext(context);
vm.runInContext(input.escapeSource, context);
context.c = { id: 42, title: input.title, student_name: input.studentName };
const rendered = vm.runInContext('`' + input.buttonTemplate + '`', context);
const decode = text => text.replace(/&(amp|lt|gt|quot|#39);/g,
    (_, name) => ({amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'"})[name]);
const attrs = {};
for (const match of rendered.matchAll(/([\w-]+)="([^"]*)"/g)) attrs[match[1]] = decode(match[2]);
context.btn = {
    dataset: {
        cpId: attrs['data-cp-id'],
        title: attrs['data-title'],
        student: attrs['data-student']
    }
};
vm.runInContext('(function() {' + attrs.onclick + '}).call(btn)', context);
process.stdout.write(JSON.stringify({
    rendered, attrs, calls,
    injection: Boolean(context.globalThis.injection || context.injection)
}));
"""


SWITCH_TAB_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = { checkpoints: 0, proposals: 0 };
const classListMap = {};
function makeElem(id) {
    const classes = new Set(['hidden']);
    classListMap[id] = classes;
    return {
        className: '',
        classList: {
            add: c => classes.add(c),
            remove: c => classes.delete(c),
            contains: c => classes.has(c)
        }
    };
}
const elements = {
    tabTeacherAppsBtn: makeElem('tabTeacherAppsBtn'),
    teacherTabApps: makeElem('teacherTabApps'),
    tabTeacherStudentsBtn: makeElem('tabTeacherStudentsBtn'),
    teacherTabStudents: makeElem('teacherTabStudents'),
    tabTeacherCheckpointsBtn: makeElem('tabTeacherCheckpointsBtn'),
    teacherTabCheckpoints: makeElem('teacherTabCheckpoints'),
    tabTeacherProposalsBtn: makeElem('tabTeacherProposalsBtn'),
    teacherTabProposals: makeElem('teacherTabProposals')
};
const context = {
    document: { getElementById: id => elements[id] || null },
    loadTeacherCheckpoints: () => { calls.checkpoints++; },
    loadTeacherProposals: () => { calls.proposals++; },
    activeTeacherTab: null
};
vm.createContext(context);
vm.runInContext(input.switchSource, context);
vm.runInContext("switchTeacherTab('checkpoints')", context);
const checkpointsOpen = !classListMap['teacherTabCheckpoints'].has('hidden');
vm.runInContext("switchTeacherTab('proposals')", context);
const proposalsOpen = !classListMap['teacherTabProposals'].has('hidden');
process.stdout.write(JSON.stringify({ calls, checkpointsOpen, proposalsOpen }));
"""


DOWNLOAD_FINALLY_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = { revoked: [], removed: 0, toasts: [] };
const context = {
    Headers,
    console: { warn() {}, error() {} },
    showToast: (...args) => calls.toasts.push(args),
    apiFetch: async () => ({
        ok: true,
        status: 200,
        headers: new Headers({}),
        blob: async () => 'test-blob'
    }),
    window: {
        URL: {
            createObjectURL: () => 'blob:cleanup-test',
            revokeObjectURL: url => calls.revoked.push(url)
        }
    },
    document: {
        body: { appendChild() {} },
        createElement: () => ({
            click() { throw new Error('simulated-click-failure'); },
            remove() { calls.removed++; }
        })
    }
};
vm.createContext(context);
vm.runInContext(input.helperSource, context);
(async () => {
    await context.downloadAuthenticatedFile(null, '/dummy/url', 'file.pdf');
    process.stdout.write(JSON.stringify({ calls }));
})().catch(err => { console.error(err); process.exitCode = 1; });
"""


def run_download(role="student", status=200, disposition=None, token="fresh-session-token", **options):
    if NODE is None:
        pytest.skip("Node is required to execute the browser compatibility regression")
    source = INDEX.read_text(encoding="utf-8")
    start = source.index("        async function apiFetch(")
    end = source.index("        // ─── 4.", start)
    escape_start = source.index("        function escapeHtml(")
    escape_end = source.index("        // Немедленная", escape_start)
    # The student and teacher templates have different classes/text but share
    # the same authenticated handler and retain their existing presentation.
    anchors = []
    for line in source.splitlines():
        if '<a href="#" data-download-url=' in line:
            anchors.append(line.strip())
    assert len(anchors) == 2, "Both checkpoint views must use authenticated downloads"
    payload = {
        "helperSource": source[start:end],
        "escapeSource": source[escape_start:escape_end],
        "anchor": anchors[0 if role == "student" else 1],
        "status": status,
        "disposition": disposition,
        "token": token,
        "fileName": "cached_report.pdf",
        **options,
    }
    result = subprocess.run(
        [NODE, "-e", NODE_HARNESS], input=json.dumps(payload), text=True, encoding="utf-8",
        capture_output=True, check=True, timeout=10,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("role", ["student", "teacher"])
def test_checkpoint_link_fetches_with_current_bearer_then_downloads_blob(role):
    result = run_download(role)
    calls = result["calls"]
    assert result["attrs"]["href"] == "#"
    assert calls["prevented"] == 1
    assert calls["requests"] == [{
        "url": "/api/checkpoints/download/7",
        "headers": {"Authorization": "Bearer fresh-session-token"},
    }]
    assert calls["downloads"] == [{"href": "blob:authenticated-file", "name": "cached_report.pdf"}]
    assert calls["blobs"] == calls["removed"] == 1
    assert calls["revoked"] == ["blob:authenticated-file"]
    assert calls["logouts"] == calls["toasts"] == []


@pytest.mark.parametrize("role", ["student", "teacher"])
@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_checkpoint_download_errors_never_save_error_response(role, status):
    calls = run_download(role, status=status)["calls"]
    assert calls["prevented"] == 1
    assert calls["blobs"] == 0
    assert calls["downloads"] == calls["revoked"] == []
    assert calls["toasts"]
    if status == 401:
        assert calls["logouts"] == [True]
        assert calls["modals"] == ["login"]
    else:
        assert calls["logouts"] == calls["modals"] == []


@pytest.mark.parametrize("role", ["student", "teacher"])
def test_checkpoint_download_network_error_stops_without_blob(role):
    calls = run_download(role, networkError=True)["calls"]
    assert calls["requests"][0]["headers"]["Authorization"] == "Bearer fresh-session-token"
    assert calls["blobs"] == 0
    assert calls["downloads"] == calls["revoked"] == []
    assert calls["toasts"]


@pytest.mark.parametrize("disposition,expected", [
    ('attachment; filename="Original report.pdf"', "Original report.pdf"),
    ("attachment; filename=report.docx", "report.docx"),
    ("attachment; filename*=utf-8''%D0%9E%D1%82%D1%87%D0%B5%D1%82.pdf", "Отчет.pdf"),
    ('attachment; filename="fallback.pdf"; filename*=UTF-8\'\'%D0%A2%D0%B5%D0%BC%D0%B0.pdf', "Тема.pdf"),
    ("attachment; filename*=UTF-8''%not-valid", "cached_report.pdf"),
])
def test_authenticated_download_preserves_server_attachment_filename(disposition, expected):
    assert run_download(disposition=disposition)["calls"]["downloads"][0]["name"] == expected


@pytest.mark.parametrize("role", ["student", "teacher"])
def test_checkpoint_filename_is_data_and_cannot_inject_click_handler(role):
    name = 'Отчет \" onclick=\"globalThis.injection = true\" <tag> & \'x\'.pdf'
    result = run_download(role, fileName=name)
    assert result["attrs"]["data-download-name"] == name
    assert result["calls"]["downloads"][0]["name"] == name
    assert result["attrs"]["onclick"] == (
        "return downloadAuthenticatedFile(event, this.dataset.downloadUrl, this.dataset.downloadName)"
    )
    assert "&quot;" in result["rendered"] and "&lt;tag&gt;" in result["rendered"]


def test_missing_session_token_never_sends_null_or_cached_authorization():
    calls = run_download(token=None, status=401)["calls"]
    assert calls["requests"] == [{"url": "/api/checkpoints/download/7", "headers": {}}]
    assert calls["downloads"] == []
    assert calls["logouts"] == [True]


def test_review_revision_button_is_data_and_cannot_inject_click_handler():
    if NODE is None:
        pytest.skip("Node is required")
    source = INDEX.read_text(encoding="utf-8")
    escape_start = source.index("        function escapeHtml(")
    escape_end = source.index("        // Немедленная", escape_start)

    button_template = None
    for line in source.splitlines():
        if 'openReviewRevisionModal' in line and '<button' in line:
            button_template = line.strip()
            break
    assert button_template is not None, "Review revision button template must exist"
    assert "onclick=\"openReviewRevisionModal(this.dataset.cpId, this.dataset.title, this.dataset.student)\"" in button_template
    assert "data-title=\"${escapeHtml(c.title || '')}\"" in button_template
    assert "data-student=\"${escapeHtml(c.student_name || '')}\"" in button_template
    assert "encodeURIComponent" not in button_template, "Must not use encodeURIComponent in onclick"

    malicious_name = 'О\'Генри " onclick="globalThis.injection = true" <script> & \'test\''
    malicious_title = 'Раздел 1 " onclick="globalThis.injection = true" <tag> & \'x\''

    payload = {
        "escapeSource": source[escape_start:escape_end],
        "buttonTemplate": button_template,
        "studentName": malicious_name,
        "title": malicious_title,
    }
    proc = subprocess.run(
        [NODE, "-e", REVISION_HARNESS], input=json.dumps(payload), text=True, encoding="utf-8",
        capture_output=True, check=True, timeout=10,
    )
    result = json.loads(proc.stdout)
    assert not result["injection"], "Injection must not execute"
    assert result["attrs"]["data-student"] == malicious_name
    assert result["attrs"]["data-title"] == malicious_title
    assert result["attrs"]["data-cp-id"] == "42"
    assert result["calls"]["opened"] == [["42", malicious_title, malicious_name]]
    assert "&quot;" in result["rendered"] and "&lt;script&gt;" in result["rendered"] and "&#39;" in result["rendered"]


def test_switch_teacher_tab_opens_tab_and_invokes_data_loaders():
    if NODE is None:
        pytest.skip("Node is required")
    source = INDEX.read_text(encoding="utf-8")
    start = source.index("        function switchTeacherTab(")
    end = source.index("        async function loadTeacherDashboard(", start)
    payload = {
        "switchSource": source[start:end],
    }
    proc = subprocess.run(
        [NODE, "-e", SWITCH_TAB_HARNESS], input=json.dumps(payload), text=True, encoding="utf-8",
        capture_output=True, check=True, timeout=10,
    )
    result = json.loads(proc.stdout)
    assert result["checkpointsOpen"] is True
    assert result["calls"]["checkpoints"] == 1
    assert result["proposalsOpen"] is True
    assert result["calls"]["proposals"] == 1


def test_download_authenticated_file_finally_cleans_up_on_error():
    if NODE is None:
        pytest.skip("Node is required")
    source = INDEX.read_text(encoding="utf-8")
    start = source.index("        async function downloadAuthenticatedFile(")
    end = source.index("        // ─── 4.", start)
    func_source = source[start:end]
    assert "finally {" in func_source
    assert "revokeObjectURL" in func_source
    assert "remove()" in func_source

    payload = {
        "helperSource": func_source,
    }
    proc = subprocess.run(
        [NODE, "-e", DOWNLOAD_FINALLY_HARNESS], input=json.dumps(payload), text=True, encoding="utf-8",
        capture_output=True, check=True, timeout=10,
    )
    result = json.loads(proc.stdout)
    assert result["calls"]["removed"] == 1
    assert result["calls"]["revoked"] == ["blob:cleanup-test"]
    assert len(result["calls"]["toasts"]) == 1
