import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import sample_reports
from qibocamp import config
from qibocamp.web.api import app, get_report_root, set_report_root
from qibocamp.reports.generator import (
    has_cached_report,
    load_cached_protocols,
    regenerate_report,
)
from qibocamp.reports.scanner import compute_filter_stats, filter_reports, scan_reports


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QIBOCAMP_CONFIG_DIR", str(tmp_path / "config"))
    test_reports = sample_reports(tmp_path / "sample_data")
    target_meta = test_reports / "10:50:56_[0]_resonator_punchout" / "meta.json"
    if target_meta.exists():
        m_data = json.loads(target_meta.read_text(encoding="utf-8"))
        m_data["tag"] = ["broken"]
        target_meta.write_text(json.dumps(m_data, indent=2), encoding="utf-8")
    set_report_root(test_reports, is_original=True)


def test_config_servers(tmp_path):
    assert config.load_servers() == [], "no file, no server: none is made up"
    new_srv = config.add_server("http://example.com:9000", name="test-qpu")
    assert new_srv["name"] == "test-qpu"
    assert new_srv["url"] == "http://example.com:9000"

    # Update
    updated = config.update_server(new_srv["id"], {"description": "Updated desc"})
    assert updated["description"] == "Updated desc"

    # URL normalization
    norm_srv = config.add_server("192.168.1.100:8000/", name="raw-ip")
    assert norm_srv["url"] == "http://192.168.1.100:8000"

    updated_norm = config.update_server(norm_srv["id"], {"url": "10.0.0.1:8080/"})
    assert updated_norm["url"] == "http://10.0.0.1:8080"

    # Protocol docs index
    docs_srv = config.add_server(
        "http://example.com:9001",
        name="docs-qpu",
        protocol_docs={"custom_routine": "custom/custom_routine.html"},
    )
    assert docs_srv["protocol_docs"]["custom_routine"] == "custom/custom_routine.html"
    updated_docs = config.update_server(
        docs_srv["id"],
        {"protocol_docs": {"custom_routine": "custom/v2.html", "rabi": "rabi.html"}},
    )
    assert updated_docs["protocol_docs"]["rabi"] == "rabi.html"
    config.delete_server(docs_srv["id"])

    # Delete
    deleted = config.delete_server(new_srv["id"])
    assert deleted is True
    config.delete_server(norm_srv["id"])


def test_scanner_and_filters():
    sample_dir = get_report_root()
    reports = scan_reports(sample_dir)
    assert len(reports) >= 3

    # Filter by query
    pi_pulse = filter_reports(reports, query="pi-pulse")
    assert len(pi_pulse) == 1
    assert "pi-pulse" in pi_pulse[0].id.lower()

    # Filter by protocol
    ssc_reports = filter_reports(reports, protocols=["single_shot_classification"])
    assert len(ssc_reports) >= 2

    # Filter stats
    stats = compute_filter_stats(reports)
    assert len(stats.protocols) > 0
    # verify protocols are sorted descending by frequency
    counts = [p.count for p in stats.protocols]
    assert counts == sorted(counts, reverse=True)
    assert "broken" in stats.tags
    assert "broken" in stats.tags
    assert len(stats.platforms) > 0
    assert len(stats.author_frequencies) > 0
    assert stats.total_reports == len(reports)

    # Filter by platform
    top_platform = stats.platforms[0].name
    plat_reports = filter_reports(reports, platforms=[top_platform])
    assert len(plat_reports) == stats.platforms[0].count
    assert all(r.platform == top_platform for r in plat_reports)

    # Filter by tag / label: the pi-pulse report comes tagged, the punchout is tagged above
    broken_reports = filter_reports(reports, tags=["broken"])
    assert len(broken_reports) == 2
    assert all("broken" in report.tags for report in broken_reports)

    # Filter by qubit
    q3_reports = filter_reports(reports, qubits=["3"])
    assert len(q3_reports) >= 1
    assert any(3 in r.targets or "3" in [str(t) for t in r.targets] for r in q3_reports)
    q3_cap_reports = filter_reports(reports, qubits=["Q3"])
    assert len(q3_cap_reports) == len(q3_reports)

    # Sort by platform
    sorted_by_platform = filter_reports(reports, sort_by="platform")
    platforms = [r.platform.lower() for r in sorted_by_platform if r.platform]
    assert platforms == sorted(platforms)

    # Filter by folder
    first_report_id = reports[0].id
    folder_reports = filter_reports(reports, folder=first_report_id)
    assert len(folder_reports) == 1
    assert folder_reports[0].id == first_report_id


def test_generator_modes():
    test_reports = get_report_root()
    rep1 = test_reports / "21:47:29_[3]_pi-pulse"
    cache_dir = rep1 / "report"
    cache_dir.mkdir(parents=True, exist_ok=True)
    proto_data = [
        {
            "id": "rabi_amplitude",
            "name": "Rabi Amplitude",
            "category": "calibration",
            "status": "success",
            "figures": [{"id": "fig1", "title": "Rabi Plot", "data": [], "layout": {}}],
            "html": "<p>Rabi results</p>",
        }
    ]
    with open(cache_dir / "protocols.json", "w", encoding="utf-8") as f:
        json.dump(proto_data, f)

    assert has_cached_report(rep1) is True
    cached_protos = load_cached_protocols(rep1)
    assert len(cached_protos) == 1
    assert "rabi" in cached_protos[0].id
    assert len(cached_protos[0].figures) > 0

    # Test error reporting when Qibocal is not available / cannot generate
    from unittest.mock import patch

    from qibocamp.reports.generator import GenerationError, generate_report_on_the_fly

    with patch(
        "qibocamp.reports.generator._generate_qibocal_protocols",
        return_value=(None, GenerationError("Qibocal is not installed in the environment.")),
    ):
        rep_unavail = test_reports / "nonexistent_run"
        rep_unavail.mkdir()
        err_protos = generate_report_on_the_fly(rep_unavail)
        assert len(err_protos) > 0
        assert err_protos[0].status == "error"
        assert "Qibocal is not installed" in (err_protos[0].error or "")


def test_regeneration_with_qibocal():
    pytest.importorskip("qibocal")
    report = get_report_root() / "21:39:54_[3]_single_shot_classification"
    regenerated = regenerate_report(report)
    assert len(regenerated) > 0
    assert regenerated[0].status == "success"
    assert len(regenerated[0].figures) > 0
    assert has_cached_report(report) is True


def test_api_endpoints():
    client = TestClient(app)

    # Health
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["reports_count"] >= 3

    # Reports list
    r = client.get("/api/reports")
    assert r.status_code == 200
    reports = r.json()
    assert len(reports) >= 3

    # Filter by tag
    r_tag = client.get("/api/reports?tag=broken")
    assert r_tag.status_code == 200
    assert len(r_tag.json()) == 2
    assert all("broken" in report["tags"] for report in r_tag.json())

    # Filter by qubit via API
    r_qubit = client.get("/api/reports?qubit=3")
    assert r_qubit.status_code == 200
    assert len(r_qubit.json()) >= 1
    # A report on a pair involving qubit 3 counts too
    def qubits(target):
        return [str(qubit) for qubit in target] if isinstance(target, list) else [str(target)]

    assert all(any("3" in qubits(target) for target in r["targets"]) for r in r_qubit.json())

    # Stats
    r = client.get("/api/reports/stats")
    assert r.status_code == 200
    stats = r.json()
    assert "protocols" in stats
    assert "date_histogram" in stats
    assert "platforms" in stats
    assert "author_frequencies" in stats
    assert stats["total_reports"] > 0

    # Filter by platform via API
    plat_name = stats["platforms"][0]["name"]
    r_plat = client.get(f"/api/reports?platform={plat_name}")
    assert r_plat.status_code == 200
    assert len(r_plat.json()) == stats["platforms"][0]["count"]

    # Filtered stats
    r_filtered_stats = client.get(f"/api/reports/stats?platform={plat_name}")
    assert r_filtered_stats.status_code == 200
    f_stats = r_filtered_stats.json()
    assert f_stats["total_reports"] == stats["platforms"][0]["count"]
    assert len(f_stats["platforms"]) == 1
    assert f_stats["platforms"][0]["name"] == plat_name

    # Filter by date range via API
    dates = [b["date"] for b in stats["date_histogram"]]
    if dates:
        start_d, end_d = dates[0], dates[-1]
        r_range = client.get(f"/api/reports?start_date={start_d}&end_date={end_d}")
        assert r_range.status_code == 200
        assert len(r_range.json()) == stats["total_reports"]

        r_range_stats = client.get(f"/api/reports/stats?start_date={start_d}&end_date={end_d}")
        assert r_range_stats.status_code == 200
        assert r_range_stats.json()["total_reports"] == stats["total_reports"]

    # Filter by folder endpoint
    r_folder = client.get(f"/api/reports?folder={reports[0]['id']}")
    assert r_folder.status_code == 200
    assert len(r_folder.json()) == 1
    assert r_folder.json()[0]["id"] == reports[0]["id"]

    # Filter stats by folder endpoint
    r_fstats = client.get(f"/api/reports/stats?folder={reports[0]['id']}")
    assert r_fstats.status_code == 200
    assert r_fstats.json()["total_reports"] == 1

    # Browse directory with scope=root
    r_browse_root = client.get("/api/server/directory/browse?scope=root")
    assert r_browse_root.status_code == 200
    assert "directories" in r_browse_root.json()

    # Single report detail
    first_id = reports[0]["id"]
    r = client.get(f"/api/reports/{first_id}")
    assert r.status_code == 200
    detail = r.json()
    assert "protocols_summary" in detail

    # Report protocols
    r = client.get(f"/api/reports/{first_id}/protocols")
    assert r.status_code == 200
    protos = r.json()
    assert len(protos) > 0
    assert "figures" in protos[0]
    assert "html" in protos[0]

    # Regenerate report
    r = client.post(f"/api/reports/{first_id}/regenerate")
    assert r.status_code == 200
    protos_regen = r.json()
    assert len(protos_regen) > 0

    # WebSocket endpoint streaming
    with client.websocket_connect(f"/ws/reports/{first_id}") as ws:
        msg1 = ws.receive_json()
        assert msg1["type"] == "metadata"
        msg2 = ws.receive_json()
        assert msg2["type"] == "queued"

    # Download full report zip
    r_full = client.get(f"/api/reports/{first_id}/download/full")
    assert r_full.status_code == 200
    assert r_full.headers["content-type"] == "application/zip"
    assert "attachment;" in r_full.headers.get("content-disposition", "")
    with zipfile.ZipFile(io.BytesIO(r_full.content)) as zf:
        namelist = zf.namelist()
        assert any("index.html" in name for name in namelist)

    # Download new platform zip
    r_new_plat = client.get(f"/api/reports/{first_id}/download/new-platform")
    assert r_new_plat.status_code == 200
    assert r_new_plat.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r_new_plat.content)) as zf:
        namelist = zf.namelist()
        assert len(namelist) > 0

    # Download old platform zip
    r_old_plat = client.get(f"/api/reports/{first_id}/download/old-platform")
    assert r_old_plat.status_code == 200
    assert r_old_plat.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r_old_plat.content)) as zf:
        namelist = zf.namelist()
        assert len(namelist) > 0

    # View meta.json
    r_meta = client.get(f"/api/reports/{first_id}/meta.json")
    assert r_meta.status_code == 200
    assert "application/json" in r_meta.headers["content-type"]
    assert r_meta.headers.get("content-disposition") == "inline"
    meta_json = r_meta.json()
    assert "platform" in meta_json

    # Download protocol data zip
    proto_id = protos[0]["id"]
    r_proto_data = client.get(f"/api/reports/{first_id}/download/data/{proto_id}")
    assert r_proto_data.status_code == 200
    assert r_proto_data.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r_proto_data.content)) as zf:
        namelist = zf.namelist()
        assert len(namelist) > 0

    # Verify execution_time is filled from meta.json
    assert protos[0]["execution_time"] is not None
    assert protos[0]["execution_time"] != "N/A"

    # Platform data endpoints
    r_plat_old = client.get(f"/api/reports/{first_id}/platform/old")
    assert r_plat_old.status_code == 200
    p_data_old = r_plat_old.json()
    assert p_data_old["platform_type"] == "old"
    assert "parameters" in p_data_old
    assert "calibration" in p_data_old

    r_plat_new = client.get(f"/api/reports/{first_id}/platform/new")
    assert r_plat_new.status_code == 200
    p_data_new = r_plat_new.json()
    assert p_data_new["platform_type"] == "new"

    # Platform raw file endpoints
    r_raw_params = client.get(f"/api/reports/{first_id}/platform/old/parameters.json")
    assert r_raw_params.status_code == 200
    assert "application/json" in r_raw_params.headers["content-type"]
    r_raw_calib = client.get(f"/api/reports/{first_id}/platform/old/calibration.json")
    assert r_raw_calib.status_code == 200

    # Non-existent report downloads
    assert client.get("/api/reports/nonexistent/download/full").status_code == 404
    assert client.get("/api/reports/nonexistent/meta.json").status_code == 404
    assert (
        client.get(f"/api/reports/{first_id}/download/data/nonexistent").status_code
        == 404
    )


def test_docs_endpoint():
    client = TestClient(app)
    # Test docs nav tree
    r_nav = client.get("/api/docs-nav")
    assert r_nav.status_code == 200
    nav_data = r_nav.json()
    assert len(nav_data) >= 4
    assert nav_data[0]["section"] == "Overview"

    # Test main page (index)
    r_index = client.get("/api/docs-content/index")
    assert r_index.status_code == 200
    assert "Qibo Ecosystem" in r_index.text
    assert "qibo.science" in r_index.text

    # Test nested subpage path
    r_sub = client.get("/api/docs-content/user-guide/quickstart")
    assert r_sub.status_code == 200
    assert "Installation" in r_sub.text

    # Test pages ending with 'd' or 'm' to prevent rstrip regressions
    r_dash = client.get("/api/docs-content/user-guide/dashboard")
    assert r_dash.status_code == 200
    assert "Multi-Server Management" in r_dash.text

    r_design = client.get("/api/docs-content/developer/design-system")
    assert r_design.status_code == 200
    assert "Color Palette" in r_design.text

    # Test minimal section table of contents pages
    for sec_path, expected_subpage in [
        ("user-guide", "quickstart"),
        ("developer", "architecture"),
        ("reference", "cli"),
    ]:
        r_sec = client.get(f"/api/docs-content/{sec_path}")
        assert r_sec.status_code == 200
        assert expected_subpage in r_sec.text

    # Test 404 for nonexistent doc
    r = client.get("/api/docs-content/nonexistent")
    assert r.status_code == 404


def test_the_root_names_the_dashboard():
    from qibocamp.config import DASHBOARD_URL

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert DASHBOARD_URL in r.text


def test_bulk_actions():
    client = TestClient(app)

    # 1. Test bulk label
    r = client.get("/api/reports")
    reports = r.json()
    assert len(reports) >= 2
    ids_to_label = [reports[0]["id"], reports[1]["id"]]

    res = client.post(
        "/api/reports/bulk-action",
        json={"action": "label", "report_ids": ids_to_label, "label": "bulk-tested"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["affected"] == 2

    # Verify both have the tag now
    r_tagged = client.get("/api/reports?tag=bulk-tested")
    assert r_tagged.status_code == 200
    assert len(r_tagged.json()) == 2

    # 2. Test bulk delete
    id_to_delete = reports[0]["id"]
    res_del = client.post(
        "/api/reports/bulk-action",
        json={"action": "delete", "report_ids": [id_to_delete]},
    )
    assert res_del.status_code == 200
    del_data = res_del.json()
    assert del_data["success"] is True
    assert del_data["affected"] == 1

    # Verify it is no longer listed
    r_after = client.get("/api/reports")
    remaining_ids = [rep["id"] for rep in r_after.json()]
    assert id_to_delete not in remaining_ids

    # 3. Test invalid action & empty label
    res_bad = client.post(
        "/api/reports/bulk-action",
        json={"action": "invalid_action", "report_ids": []},
    )
    assert res_bad.status_code == 400

    res_empty_label = client.post(
        "/api/reports/bulk-action",
        json={"action": "label", "report_ids": [], "label": "   "},
    )
    assert res_empty_label.status_code == 400


def test_single_report_actions():
    client = TestClient(app)
    r = client.get("/api/reports")
    reports = r.json()
    assert len(reports) >= 2
    rep_id = reports[0]["id"]

    # Test single label
    res_label = client.post(
        f"/api/reports/{rep_id}/label", json={"label": "single-tagged"}
    )
    assert res_label.status_code == 200
    assert res_label.json()["success"] is True

    r_verify = client.get(f"/api/reports/{rep_id}")
    assert r_verify.status_code == 200
    assert "single-tagged" in r_verify.json()["tags"]

    # Test single delete
    res_del = client.delete(f"/api/reports/{rep_id}")
    assert res_del.status_code == 200
    assert res_del.json()["success"] is True

    r_after = client.get(f"/api/reports/{rep_id}")
    assert r_after.status_code == 404


def test_remove_label_and_author_actions():
    client = TestClient(app)
    r = client.get("/api/reports")
    reports = r.json()
    assert len(reports) >= 2
    target_rep = reports[0]
    target_id = target_rep["id"]

    # 1. Add a tag first
    res_add = client.post(
        f"/api/reports/{target_id}/label", json={"label": "to-remove"}
    )
    assert res_add.status_code == 200

    r_verify = client.get(f"/api/reports/{target_id}")
    assert "to-remove" in r_verify.json()["tags"]

    # 2. Delete the tag via single label delete endpoint
    res_del_tag = client.delete(f"/api/reports/{target_id}/label/to-remove")
    assert res_del_tag.status_code == 200
    assert res_del_tag.json()["success"] is True

    r_verify2 = client.get(f"/api/reports/{target_id}")
    assert "to-remove" not in r_verify2.json()["tags"]

    # 3. Test bulk unlabel
    # Add label to 2 reports
    id1, id2 = reports[0]["id"], reports[1]["id"]
    client.post(
        "/api/reports/bulk-action",
        json={"action": "label", "report_ids": [id1, id2], "label": "bulk-temp"},
    )
    # Remove via bulk unlabel
    res_bulk_unlabel = client.post(
        "/api/reports/bulk-action",
        json={"action": "unlabel", "report_ids": [id1, id2], "label": "bulk-temp"},
    )
    assert res_bulk_unlabel.status_code == 200
    assert res_bulk_unlabel.json()["success"] is True
    assert res_bulk_unlabel.json()["affected"] == 2

    # 4. Test single author update
    res_author = client.put(
        f"/api/reports/{target_id}/author", json={"author": "Alice Specialist"}
    )
    assert res_author.status_code == 200
    assert res_author.json()["success"] is True

    r_verify_author = client.get(f"/api/reports/{target_id}")
    assert r_verify_author.json()["author"] == "Alice Specialist"

    # 5. Test bulk author update
    res_bulk_author = client.post(
        "/api/reports/bulk-action",
        json={"action": "author", "report_ids": [id1, id2], "author": "Quantum Team"},
    )
    assert res_bulk_author.status_code == 200
    assert res_bulk_author.json()["affected"] == 2

    r1 = client.get(f"/api/reports/{id1}").json()
    r2 = client.get(f"/api/reports/{id2}").json()
    assert r1["author"] == "Quantum Team"
    assert r2["author"] == "Quantum Team"


def test_author_identities_and_search_index():
    from qibocamp.config import resolve_author_identity

    # 1. Test resolve_author_identity helper
    identities = {
        "Alice": ["alice", "a.smith", "alicesmith"],
        "Bob": ["bob", "b.jones"],
    }
    assert resolve_author_identity("a.smith", identities) == "Alice"
    assert resolve_author_identity("Alice", identities) == "Alice"
    assert resolve_author_identity("BOB", identities) == "Bob"
    assert resolve_author_identity("UnknownPerson", identities) == "UnknownPerson"
    assert resolve_author_identity("Unknown", identities) == "Unknown"
    assert resolve_author_identity(None, identities) == "Unknown"

    # 2. Test server config with author_identities
    srv = config.add_server(
        "http://127.0.0.1:8001",
        name="alias-server",
        author_identities={"Dr. Quantum": ["alecandido", "qibo_user"]},
    )
    assert "author_identities" in srv
    assert srv["author_identities"]["Dr. Quantum"] == ["alecandido", "qibo_user"]

    # 3. Test scanner applies author_identities and produces search_index
    test_reports = get_report_root()
    reports = scan_reports(test_reports, author_identities=srv["author_identities"])
    assert len(reports) > 0
    first = reports[0]
    assert hasattr(first, "search_index")
    assert isinstance(first.search_index, str)
    assert len(first.search_index) > 0
    # verify search_index is lowercase and contains keywords
    assert first.platform.lower() in first.search_index
    assert first.id.lower() in first.search_index

    # 4. Test compute_filter_stats and filter_reports with author_identities
    from qibocamp.models import ReportSummary
    from qibocamp.reports.scanner import compute_filter_stats, filter_reports

    dummy_reports = [
        ReportSummary(
            id="rep1",
            title="Rep 1",
            path="rep1",
            date="2024-01-01T00:00:00",
            author="alecandido",
        ),
        ReportSummary(
            id="rep2",
            title="Rep 2",
            path="rep2",
            date="2024-01-02T00:00:00",
            author="Dr. Quantum",
        ),
        ReportSummary(
            id="rep3",
            title="Rep 3",
            path="rep3",
            date="2024-01-03T00:00:00",
            author="qibo_user",
        ),
        ReportSummary(
            id="rep4",
            title="Rep 4",
            path="rep4",
            date="2024-01-04T00:00:00",
            author="Bob",
        ),
    ]
    # Stats aggregation
    stats = compute_filter_stats(
        dummy_reports, author_identities=srv["author_identities"]
    )
    assert "Dr. Quantum" in stats.authors
    assert "alecandido" not in stats.authors
    assert "qibo_user" not in stats.authors
    assert len(stats.authors) == 2  # Dr. Quantum and Bob
    dr_freq = next(
        (f for f in stats.author_frequencies if f.name == "Dr. Quantum"), None
    )
    assert dr_freq is not None
    assert dr_freq.count == 3  # alecandido, Dr. Quantum, qibo_user

    # Filtering by canonical name matches alias reports
    filtered = filter_reports(
        dummy_reports,
        authors=["Dr. Quantum"],
        author_identities=srv["author_identities"],
    )
    assert len(filtered) == 3
    assert {r.id for r in filtered} == {"rep1", "rep2", "rep3"}

    # Filtering by alias name also matches the canonical group
    filtered_alias = filter_reports(
        dummy_reports,
        authors=["alecandido"],
        author_identities=srv["author_identities"],
    )
    assert len(filtered_alias) == 3


def test_pagination_and_cache_invalidation(monkeypatch):
    # Every scan checks the tree, rather than trusting the last walk for a while.
    monkeypatch.setenv("QIBOCAMP_SCAN_INTERVAL", "0")
    client = TestClient(app)
    test_reports = get_report_root()

    # 1. Test pagination query parameters
    r_p1 = client.get("/api/reports?page=1&page_size=2")
    assert r_p1.status_code == 200
    data_p1 = r_p1.json()
    assert "items" in data_p1
    assert "total" in data_p1
    assert data_p1["page"] == 1
    assert data_p1["page_size"] == 2
    assert len(data_p1["items"]) == 2
    assert data_p1["total"] >= 3
    assert data_p1["total_pages"] >= 2
    assert r_p1.headers.get("X-Total-Count") == str(data_p1["total"])
    assert r_p1.headers.get("X-Page") == "1"

    # Page 2
    r_p2 = client.get("/api/reports?page=2&page_size=2")
    assert r_p2.status_code == 200
    data_p2 = r_p2.json()
    assert data_p2["page"] == 2
    assert len(data_p2["items"]) >= 1
    # Check that items on page 1 and page 2 are distinct
    p1_ids = {item["id"] for item in data_p1["items"]}
    p2_ids = {item["id"] for item in data_p2["items"]}
    assert p1_ids.isdisjoint(p2_ids)

    # 2. Test backend caching and invalidation
    from qibocamp.reports.scanner import _REPORT_CACHE, invalidate_report_cache

    # First scan caches the results
    scan1 = scan_reports(test_reports)
    # Second scan returns the exact cached object
    scan2 = scan_reports(test_reports)
    assert scan1 is scan2

    # Invalidate cache manually
    invalidate_report_cache()
    scan3 = scan_reports(test_reports)
    assert len(scan3) == len(scan1)

    # Invalidation on file modification
    first_report_dir = Path(scan1[0].path)
    meta_file = first_report_dir / "meta.json"
    if meta_file.is_file():
        # Update modification time to future
        new_mtime = max(meta_file.stat().st_mtime, _REPORT_CACHE["last_mtime"]) + 10.0
        import os

        os.utime(meta_file, (new_mtime, new_mtime))
        scan_modified = scan_reports(test_reports)
        assert scan_modified is not scan3


def test_server_directory_management(tmp_path):
    client = TestClient(app)
    test_reports = get_report_root()

    # 1. Test get directory info
    res = client.get("/api/server/directory")
    assert res.status_code == 200
    dir_info = res.json()
    assert dir_info["original_root"] == str(test_reports)
    assert dir_info["current_root"] == str(test_reports)
    assert dir_info["relative_current"] == ""
    assert dir_info["reports_count"] >= 3

    # 2. Browse directory (root): report folders are leaves and omitted
    browse_res = client.get("/api/server/directory/browse")
    assert browse_res.status_code == 200
    browse_data = browse_res.json()
    assert browse_data["current_browse_path"] == ""
    assert browse_data["parent_path"] is None
    assert browse_data["is_active_root"] is True
    assert browse_data["reports_count"] >= 3
    # Report directories like pi-pulse must NOT be returned as directory options
    sub_names = [d["name"] for d in browse_data["directories"]]
    assert not any("pi-pulse" in n for n in sub_names)

    # 3. Create a container folder containing a report folder
    batch_dir = test_reports / "experiments_batch"
    run_dir = batch_dir / "run_1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(
        json.dumps({"title": "Sub Run"}), encoding="utf-8"
    )

    browse_after = client.get("/api/server/directory/browse")
    assert browse_after.status_code == 200
    after_data = browse_after.json()
    found_batch = next(
        (d for d in after_data["directories"] if d["name"] == "experiments_batch"),
        None,
    )
    assert found_batch is not None
    assert found_batch["reports_count"] == 1

    # 4. Browse inside container: child report run_1 is a leaf, so directories is empty
    browse_inside = client.get("/api/server/directory/browse?path=experiments_batch")
    assert browse_inside.status_code == 200
    inside_data = browse_inside.json()
    assert inside_data["current_browse_path"] == "experiments_batch"
    assert inside_data["parent_path"] == ""
    assert len(inside_data["breadcrumbs"]) == 2
    assert len(inside_data["directories"]) == 0
    assert inside_data["reports_count"] == 1

    # 5. Attempting to select a report leaf folder as root is rejected (400)
    leaf_change = client.post(
        "/api/server/directory", json={"path": "experiments_batch/run_1"}
    )
    assert leaf_change.status_code == 400

    # 6. Change server directory to container folder
    change_res = client.post(
        "/api/server/directory", json={"path": "experiments_batch"}
    )
    assert change_res.status_code == 200
    changed_info = change_res.json()
    assert changed_info["current_root"] == str(batch_dir)
    assert changed_info["relative_current"] == "experiments_batch"
    assert changed_info["reports_count"] == 1

    # Invalidate / verify reports list now only reflects this subfolder
    reports_res = client.get("/api/reports")
    assert reports_res.status_code == 200
    reports = reports_res.json()
    assert len(reports) == 1

    # 7. Change server directory back to root
    reset_res = client.post("/api/server/directory", json={"path": ""})
    assert reset_res.status_code == 200
    reset_info = reset_res.json()
    assert reset_info["current_root"] == str(test_reports)
    assert reset_info["relative_current"] == ""
    assert reset_info["reports_count"] >= 3

    # 7. Security: Browsing or changing outside original root is forbidden (403)
    forbidden_browse = client.get("/api/server/directory/browse?path=../../")
    assert forbidden_browse.status_code == 403

    forbidden_change = client.post("/api/server/directory", json={"path": "../../"})
    assert forbidden_change.status_code == 403

    # 8. Non-existent path returns 404 on browse and 400 on change
    not_found_browse = client.get(
        "/api/server/directory/browse?path=nonexistent_folder_xyz"
    )
    assert not_found_browse.status_code == 404

    not_found_change = client.post(
        "/api/server/directory", json={"path": "nonexistent_folder_xyz"}
    )
    assert not_found_change.status_code == 400


@pytest.mark.parametrize("root_path", [".cache/agent/runs/platinum", ".cache"])
def test_server_discovery_beneath_ignored_ancestor(tmp_path, root_path):
    root = tmp_path / root_path
    report_id = "sessions/cdb13be1d07247478469c9538e1556f7"
    report_dir = root / report_id
    report_dir.mkdir(parents=True)
    (report_dir / "meta.json").write_text(
        json.dumps({"title": "Session report"}), encoding="utf-8"
    )
    for name in (".hidden", ".cache", "build"):
        excluded = root / name / "nested"
        excluded.mkdir(parents=True)
        (excluded / "meta.json").write_text("{}", encoding="utf-8")
    set_report_root(root, is_original=True)
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["reports_count"] == 1
    assert client.get("/api/server/directory").json()["reports_count"] == 1
    reports = client.get("/api/reports")
    assert reports.status_code == 200
    assert [report["id"] for report in reports.json()] == [report_id]
    detail = client.get(f"/api/reports/{report_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == report_id

    for scope in ("original", "root"):
        browse = client.get("/api/server/directory/browse", params={"scope": scope})
        assert browse.status_code == 200
        assert browse.json()["reports_count"] == 1
        assert [
            (entry["name"], entry["reports_count"], entry["has_subdirs"])
            for entry in browse.json()["directories"]
        ] == [("sessions", 1, False)]
        sessions = client.get(
            "/api/server/directory/browse", params={"path": "sessions", "scope": scope}
        )
        assert sessions.status_code == 200
        assert sessions.json()["reports_count"] == 1
        assert sessions.json()["directories"] == []

    for name in (".hidden", ".cache", "build"):
        assert client.get(f"/api/reports/{name}/nested").status_code == 404
        assert client.get(
            "/api/server/directory/browse", params={"path": name}
        ).status_code == 404

    leaf_change = client.post("/api/server/directory", json={"path": report_id})
    assert leaf_change.status_code == 400
    change = client.post("/api/server/directory", json={"path": "sessions"})
    assert change.status_code == 200
    assert change.json()["reports_count"] == 1
    assert client.get("/api/reports").json()[0]["id"] == report_dir.name
    reset = client.post("/api/server/directory", json={"path": ""})
    assert reset.status_code == 200
    assert reset.json()["reports_count"] == 1


def test_archive_workflow(tmp_path):
    client = TestClient(app)
    root = get_report_root()

    # 1. Initially no archives exist
    res = client.get("/api/archives")
    assert res.status_code == 200
    assert res.json() == []

    # Get active reports to select from
    reports_res = client.get("/api/reports")
    assert reports_res.status_code == 200
    initial_reports = reports_res.json()
    assert len(initial_reports) >= 2

    report_1_id = initial_reports[0]["id"]
    report_2_id = initial_reports[1]["id"]
    initial_count = len(initial_reports)

    # 2. Create archive with remove_from_active=True
    create_payload = {
        "report_ids": [report_1_id, report_2_id],
        "name": "Benchmark Qubits Archive",
        "description": "Weekly calibration benchmarks",
        "filters": {
            "tags": ["broken"],
            "protocols": ["resonator_punchout"]
        },
        "remove_from_active": True
    }
    create_res = client.post("/api/archives", json=create_payload)
    assert create_res.status_code == 200
    archive_data = create_res.json()
    archive_id = archive_data["id"]
    assert archive_data["name"] == "Benchmark Qubits Archive"
    assert archive_data["description"] == "Weekly calibration benchmarks"
    assert archive_data["report_count"] == 2
    assert archive_data["size_bytes"] > 0
    assert archive_data["filters"]["tags"] == ["broken"]
    assert archive_data["zip_filename"] == f"{archive_id}.zip"
    assert set(archive_data["report_ids"]) == {report_1_id, report_2_id}

    # 3. Active reports list should now have 2 fewer reports, and .archive must NOT be scanned
    after_archive_res = client.get("/api/reports")
    assert after_archive_res.status_code == 200
    remaining_reports = after_archive_res.json()
    assert len(remaining_reports) == initial_count - 2
    remaining_ids = {r["id"] for r in remaining_reports}
    assert report_1_id not in remaining_ids
    assert report_2_id not in remaining_ids
    # Ensure no archive directory was parsed as report
    assert not any(".archive" in r["id"] or "archive" in r["id"].lower() for r in remaining_reports)

    # 4. GET /api/archives lists the new archive
    list_res = client.get("/api/archives")
    assert list_res.status_code == 200
    archives = list_res.json()
    assert len(archives) == 1
    assert archives[0]["id"] == archive_id

    # 5. GET /api/archives/{id}
    meta_res = client.get(f"/api/archives/{archive_id}")
    assert meta_res.status_code == 200
    assert meta_res.json()["id"] == archive_id

    # 6. GET /api/archives/{id}/index - Zero decompression peaking
    index_res = client.get(f"/api/archives/{archive_id}/index")
    assert index_res.status_code == 200
    index_items = index_res.json()
    assert len(index_items) == 2
    for item in index_items:
        assert "id" in item
        assert "date" in item
        assert "author" in item
        assert "platform" in item
        assert "protocols" in item
        assert "qubits" in item
        assert "tags" in item
        assert item["size_bytes"] > 0

    # 7. GET /api/archives/{id}/download
    download_res = client.get(f"/api/archives/{archive_id}/download")
    assert download_res.status_code == 200
    assert download_res.headers["content-type"] == "application/zip"
    assert len(download_res.content) > 0
    # Verify zip content structure
    with zipfile.ZipFile(io.BytesIO(download_res.content)) as zf:
        namelist = zf.namelist()
        assert any(n.startswith(report_1_id) for n in namelist)
        assert any(n.startswith(report_2_id) for n in namelist)

    # 8. PATCH /api/archives/{id}
    patch_res = client.patch(
        f"/api/archives/{archive_id}",
        json={"name": "Updated Archive Title", "description": "New notes"}
    )
    assert patch_res.status_code == 200
    patched_data = patch_res.json()
    assert patched_data["name"] == "Updated Archive Title"
    assert patched_data["description"] == "New notes"

    # 9. POST /api/archives/{id}/restore - restore only report_1_id first
    restore_part = client.post(
        f"/api/archives/{archive_id}/restore",
        json={"report_ids": [report_1_id], "delete_after_restore": False}
    )
    assert restore_part.status_code == 200
    part_json = restore_part.json()
    assert part_json["restored_count"] == 1
    assert part_json["restored_ids"] == [report_1_id]

    # Check that report_1 is now back in active reports
    rep_check = client.get("/api/reports")
    assert rep_check.status_code == 200
    cur_ids = {r["id"] for r in rep_check.json()}
    assert report_1_id in cur_ids
    assert report_2_id not in cur_ids

    # Restore the remaining report with delete_after_restore=True
    restore_all = client.post(
        f"/api/archives/{archive_id}/restore",
        json={"report_ids": [report_2_id], "delete_after_restore": True}
    )
    assert restore_all.status_code == 200
    all_json = restore_all.json()
    assert all_json["restored_count"] == 1
    assert all_json["archive_deleted"] is True

    # Check both reports are now back in active reports
    rep_check2 = client.get("/api/reports")
    assert rep_check2.status_code == 200
    cur_ids2 = {r["id"] for r in rep_check2.json()}
    assert report_1_id in cur_ids2
    assert report_2_id in cur_ids2
    assert len(cur_ids2) == initial_count

    # Archive should now be deleted because delete_after_restore was True
    archives_after = client.get("/api/archives").json()
    assert len(archives_after) == 0

    # 10. Delete non-existent archive -> 404
    del_404 = client.delete(f"/api/archives/{archive_id}")
    assert del_404.status_code == 404

    # 11. Create archive with delete and then DELETE endpoint
    c_res2 = client.post(
        "/api/archives",
        json={"report_ids": [report_1_id], "name": "Temp Archive", "remove_from_active": False}
    )
    assert c_res2.status_code == 200
    arc2_id = c_res2.json()["id"]
    del_res = client.delete(f"/api/archives/{arc2_id}")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True


def test_protocol_execution_order_sorting(tmp_path: Path):
    """Test protocol execution order sorting in report view (Issue #3)."""
    from qibocamp.reports.generator import (
        get_execution_order,
        load_cached_protocols,
        get_report_protocols,
        sort_protocols_by_execution_order,
    )
    from qibocamp.reports.scanner import get_report_detail
    from qibocamp.models import ProtocolDetail

    # 1. Test real sample data with 9 routines
    sample_dir = tmp_path / "sample_data" / "21:47:29_[3]_pi-pulse"
    assert sample_dir.is_dir()

    expected_order = [
        "qubit_spectroscopy-0",
        "rabi_amplitude_signal-0",
        "single_shot_classification-0",
        "rabi_amplitude-0",
        "ramsey-0",
        "single_shot_classification-1",
        "drag_simple-0",
        "flipping-0",
        "allxy-0",
    ]

    # Verify get_execution_order
    order = get_execution_order(sample_dir)
    assert order == expected_order

    # Verify load_cached_protocols preserves execution order (not alphabetical where allxy-0 would be first)
    cached_protos = load_cached_protocols(sample_dir)
    assert [p.id for p in cached_protos] == expected_order
    assert cached_protos[0].id == "qubit_spectroscopy-0"
    assert cached_protos[-1].id == "allxy-0"

    # Verify get_report_protocols
    rep_protos = get_report_protocols(sample_dir)
    assert [p.id for p in rep_protos] == expected_order

    # Verify get_report_detail protocols_summary
    detail = get_report_detail(tmp_path / "sample_data", "21:47:29_[3]_pi-pulse")
    assert detail is not None
    assert [p.id for p in detail.protocols_summary] == expected_order

    # Verify HTTP API returns protocols in execution order
    client = TestClient(app)
    r = client.get("/api/reports/21:47:29_[3]_pi-pulse/protocols")
    assert r.status_code == 200
    api_protos = r.json()
    assert [p["id"] for p in api_protos] == expected_order

    # 2. Test fallback to meta.json stats (effective backup) when history.json is absent
    test_dir_meta = tmp_path / "test_meta_backup"
    test_dir_meta.mkdir()
    meta_content = {
        "stats": {
            "routine_omega-0": {"acquisition": 10.0, "fit": 2.0},
            "routine_alpha-0": {"acquisition": 5.0, "fit": 1.0},
            "routine_beta-0": {"acquisition": 2.5, "fit": 0.5},
        }
    }
    with open(test_dir_meta / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta_content, f)

    meta_order = get_execution_order(test_dir_meta)
    assert meta_order == ["routine_omega-0", "routine_alpha-0", "routine_beta-0"]

    # Sort protocol details
    unsorted = [
        ProtocolDetail(id="routine_alpha-0", name="Alpha", category="calibration"),
        ProtocolDetail(id="routine_beta-0", name="Beta", category="calibration"),
        ProtocolDetail(id="routine_omega-0", name="Omega", category="calibration"),
    ]
    sorted_res = sort_protocols_by_execution_order(unsorted, test_dir_meta)
    assert [p.id for p in sorted_res] == ["routine_omega-0", "routine_alpha-0", "routine_beta-0"]

    # 3. Test authoritative source precedence (history.json over meta.json)
    test_dir_both = tmp_path / "test_authoritative"
    test_dir_both.mkdir()
    with open(test_dir_both / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta_content, f)  # omega, alpha, beta
    with open(test_dir_both / "history.json", "w", encoding="utf-8") as f:
        json.dump(["routine_beta-0", "routine_alpha-0", "routine_omega-0"], f)

    both_order = get_execution_order(test_dir_both)
    assert both_order == ["routine_beta-0", "routine_alpha-0", "routine_omega-0"]

    # 4. Test flexible matching (dash/underscore, stripped suffix, and unlisted)
    flex_protos = [
        ProtocolDetail(id="routine_unlisted", name="Unlisted", category="calibration"),
        ProtocolDetail(id="routine_omega_0", name="Omega", category="calibration"),
        ProtocolDetail(id="routine_alpha", name="Alpha", category="calibration"),
    ]
    flex_sorted = sort_protocols_by_execution_order(flex_protos, test_dir_meta)
    assert [p.id for p in flex_sorted] == ["routine_omega_0", "routine_alpha", "routine_unlisted"]


def test_every_documentation_page_is_listed_and_served():
    from qibocamp.web.docs import documented_pages

    client = TestClient(app)
    files = {str(p.relative_to("docs").with_suffix("")).removesuffix("/index") for p in Path("docs").rglob("*.md")}
    assert files == documented_pages(), "a page missing from the navigation is not served"
    for page in sorted(files):
        assert client.get(f"/api/docs-content/{page}").status_code == 200, page
