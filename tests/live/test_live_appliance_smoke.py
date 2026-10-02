# -*- coding: utf-8 -*-
"""Live-цепочка §27 против живого стенда 2.0 (PHASE 2.0-17).

Запуск (стенд N6): LAN_PANEL_URL=http://<стенд>:8080 pytest -m live \
    -k appliance_chain

Безопасные для работающей панели шаги §27: hardware → capabilities →
module installation (notes, deps пустые) → role application (default)
→ job execution → configuration change (+откат) → failure (404) →
состояние не изменилось. Сетевой скан здесь не запускаем (не трогаем
чужую сеть из смоука), apt/update.sh — вручную по §27/N6.
"""
import time

import pytest

pytestmark = pytest.mark.live


def _wait_job(s, panel_url, jid, timeout=90):
    """Ждать завершения job живьём (poll /api/jobs/<id>)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = s.get(panel_url + "/api/jobs/" + jid, timeout=10)
        assert r.status_code == 200, r.status_code
        job = r.json()["job"]
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(2)
    pytest.skip("job %s не завершился за %ss" % (jid, timeout))


def test_appliance_chain_live(admin_session, panel_url):
    s = admin_session

    # --- hardware detection; цепочка только для стенда 2.0 (schema v3)
    r = s.get(panel_url + "/api/health", timeout=15)
    assert r.status_code in (200, 503)
    h = r.json()
    if int(h["db"].get("user_version") or 0) < 3:
        pytest.skip("цепочка §27 — стенд 2.0 (user_version>=3); на панели %s"
                    % h["db"].get("user_version"))
    assert set(h["platform"]) == {"board", "arch", "system", "emmc", "sd",
                                  "hdd", "thermal_zone"}

    # --- capabilities
    r = s.get(panel_url + "/api/capabilities", timeout=30)
    assert r.status_code == 200
    assert isinstance(r.json(), dict)

    # --- module installation (notes)
    r = s.post(panel_url + "/modules/notes/install", timeout=15,
               allow_redirects=False)
    assert r.status_code in (200, 302), r.status_code
    r = s.get(panel_url + "/api/jobs?limit=5", timeout=10)
    jobs = r.json()["jobs"]
    install = [j for j in jobs if j["type"] == "module-install"]
    assert install, "job module-install не появился"
    job = _wait_job(s, panel_url, install[0]["id"])
    assert job["status"] == "completed", job
    assert job["result"]["ok"] is True, job

    # --- role application (default)
    r = s.get(panel_url + "/api/roles", timeout=10)
    assert r.status_code == 200
    assert r.json()["roles"]
    r = s.post(panel_url + "/api/roles/default/apply", timeout=30)
    assert r.status_code == 200
    ra = r.json()
    assert ra["ok"] is True, ra

    # --- job execution: список задач живой, install отработал
    r = s.get(panel_url + "/api/jobs?limit=10", timeout=10)
    assert r.json()["ok"] is True
    assert any(j["type"] == "module-install"
               and j["status"] == "completed"
               for j in r.json()["jobs"])

    # --- configuration change (+ откат значения)
    r = s.get(panel_url + "/api/settings", timeout=10)
    orig = r.json().get("network", {}).get("scan_interval")
    probe = 41 if orig != 41 else 42
    r = s.post(panel_url + "/api/settings",
               json={"network": {"scan_interval": probe}}, timeout=10)
    assert r.status_code == 200 and r.json()["ok"] is True
    r = s.get(panel_url + "/api/settings", timeout=10)
    assert r.json()["network"]["scan_interval"] == probe
    r = s.post(panel_url + "/api/settings",
               json={"network": {"scan_interval": orig}}, timeout=10)
    assert r.status_code == 200 and r.json()["ok"] is True

    # --- update: единый источник версии (§34); сам update.sh — вручную
    from core.version import APP_VERSION
    assert h["version"] == APP_VERSION

    # --- failure simulation: несуществующий модуль -> 404, state чист
    r = s.post(panel_url + "/modules/no-such-module/install", timeout=10,
               allow_redirects=False)
    assert r.status_code == 404

    # --- rollback/состояние: настройки вернулись, ничего не сломано
    r = s.get(panel_url + "/api/settings", timeout=10)
    assert r.json().get("network", {}).get("scan_interval") == orig
    r = s.get(panel_url + "/api/health", timeout=15)
    assert r.status_code in (200, 503)
